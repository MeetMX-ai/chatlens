import logging
import queue
import threading
import time
import uuid
from typing import Dict, Any, List

from chatlens.event_bus import get_event_bus

# G4-2.1: 业务指标埋点
try:
    from chatlens._metrics import REGISTRY
except Exception:  # pragma: no cover
    REGISTRY = None  # type: ignore[assignment]

logger = logging.getLogger("chatlens.ide_tasks")


class IDETaskQueue:
    """
    任务队列 + 信号推送。

    信号机制（AC1）：
    - 每个 create() 调用时通过 self._signal_queue.put(event) 推送事件
    - IDE AI 端可以调用 subscribe() 注册监听器，立即收到新任务通知
    - 不依赖 SSE/HTTP，纯进程内 queue.Queue，零网络开销
    - 监听器崩溃/未注册时，create() 仍正常入队，事件不丢失（最多暂存 100 条）
    """

    _MAX_BACKLOG = 100  # 信号事件最大堆积数（防止监听器全挂时内存爆炸）

    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # 信号推送：每个监听器持有一个 queue.Queue
        self._subscribers: List["queue.Queue"] = []
        self._sub_lock = threading.Lock()
        # 事件 backlog（监听器断线重连时可重放）
        self._backlog: List[Dict[str, Any]] = []
        self._backlog_lock = threading.Lock()
        # 4.1 (AC1.3): 在途线程追踪（graceful shutdown 用）
        self._inflight: set = set()
        # 事件总线：用于 SSE 推送和 webhook 回调
        self._event_bus = get_event_bus()
        # GA 引用（用于读取 webhook_url 配置）
        self._ga_ref = None

    def track(self, t: "threading.Thread") -> None:
        """4.1 (AC1.3): 把线程加进 in-flight 集合（用于 graceful shutdown）。"""
        if t is None:
            return
        self._inflight.add(t)

    def untrack(self, t: "threading.Thread") -> None:
        """4.1 (AC1.3): 线程退出时从 in-flight 集合移除。"""
        if t is None:
            return
        try:
            self._inflight.discard(t)
        except Exception:  # pragma: no cover
            pass

    def mark_interrupted(self, task_id: str) -> None:
        """4.1 (AC1.4): 标记任务为 interrupted（shutdown 时调用）。

        与 ``mark_failed`` 区别：明确表达"被 SIGTERM 强制中断"语义，
        客户端可据此判断"任务未完成但服务端正常退出"。
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task["status"] = "interrupted"
                task["result"] = {"error": "task interrupted by graceful shutdown"}
        # 推送信号（任务中断）
        try:
            self._emit(
                {
                    "type": "task_interrupted",
                    "task_id": task_id,
                    "reason": "graceful_shutdown",
                }
            )
        except Exception as e:  # pragma: no cover
            logger.warning("IDE 任务中断信号推送失败: %s", e)

    def mark_all_interrupted(self) -> int:
        """4.1 (AC1.4): 把所有 pending 任务标记为 interrupted（drain 前调用）。

        Returns:
            标记的任务数。
        """
        count = 0
        with self._lock:
            pending = [tid for tid, t in self._tasks.items() if t.get("status") == "pending"]
        for tid in pending:
            self.mark_interrupted(tid)
            count += 1
        return count

    def drain_threads(self, timeout: float = 25.0) -> int:
        """4.1 (AC1.4): 等待所有 in-flight 线程退出，超时返回剩余数量。"""
        alive = [t for t in list(self._inflight) if t.is_alive()]
        for t in alive:
            t.join(timeout=timeout)
        still_alive = [t for t in alive if t.is_alive()]
        return len(still_alive)

    def _emit(self, event: Dict[str, Any]) -> None:
        """向所有订阅者推送事件，并写入 backlog。"""
        with self._backlog_lock:
            self._backlog.append(event)
            # 限制 backlog 长度
            if len(self._backlog) > self._MAX_BACKLOG:
                self._backlog = self._backlog[-self._MAX_BACKLOG :]
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                # 监听器处理太慢，跳过这条（避免阻塞 create）
                logger.warning("IDE 任务信号队列已满，丢弃事件: %s", event.get("type"))

    def subscribe(self, maxsize: int = 50) -> "queue.Queue":
        """注册一个监听器，返回其专属队列。

        返回的 queue.Queue 收到的事件类型：
        - {"type": "task_created", "task_id": ..., "group_name": ..., ...}
        - {"type": "task_completed", "task_id": ..., ...}
        - {"type": "task_failed", "task_id": ..., "error": ...}
        """
        q: "queue.Queue" = queue.Queue(maxsize=maxsize)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def drain_backlog(self) -> List[Dict[str, Any]]:
        """拉取并清空 backlog（用于断线重连后补偿）。"""
        with self._backlog_lock:
            events = list(self._backlog)
            self._backlog.clear()
        return events

    def create(
        self,
        group_name: str,
        theme: str = "scrapbook",
        fmt: str = "jpg",
        message_count: int = 0,
    ) -> Dict[str, Any]:
        task_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "group_name": group_name,
                "theme": theme,
                "fmt": fmt,
                "status": "pending",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "message_count": message_count,
                "result": None,
            }
        # G4-2.1: 业务指标埋点
        try:
            if REGISTRY is not None:
                REGISTRY.ide_tasks_total.inc(group=group_name, fmt=fmt)
                REGISTRY.ide_tasks_active.set(len(self._inflight))
        except Exception:  # pragma: no cover
            logger.debug("ide_tasks_total 埋点失败", exc_info=True)
        # 推送信号（新任务）— 优化 1 (AC6 容错)：_emit 失败不影响主流程
        try:
            self._emit(
                {
                    "type": "task_created",
                    "task_id": task_id,
                    "group_name": group_name,
                    "theme": theme,
                    "fmt": fmt,
                    "message_count": message_count,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        except Exception as e:
            logger.warning("IDE 任务信号推送失败（不影响任务创建）: %s", e)
        # 事件总线：发布 task_created 事件（SSE 订阅者）
        try:
            self._event_bus.publish({
                "type": "task_created",
                "task_id": task_id,
                "group_name": group_name,
                "theme": theme,
                "fmt": fmt,
                "message_count": message_count,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:
            logger.warning("事件总线推送失败（不影响任务创建）: %s", e)
        # Webhook 回调
        try:
            self._fire_webhook({
                "type": "task_created",
                "task_id": task_id,
                "group_name": group_name,
                "theme": theme,
                "fmt": fmt,
                "message_count": message_count,
            })
        except Exception as e:
            logger.warning("Webhook 触发失败（不影响任务创建）: %s", e)
        return {"success": True, "task_id": task_id, "message_count": message_count}

    def get(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            # G4-2.2: APM 上报 — TaskNotFoundError 路径 (4xx 不走 fallback_error_handler,
            # 在 IDE 任务查询的"找不到"分支单独记录, 便于追踪大量 404 异常)
            try:
                from chatlens._apm import report_error
                from chatlens.errors import TaskNotFoundError

                report_error(
                    TaskNotFoundError(f"任务不存在: {task_id}"),
                    request_id="-",
                )
            except Exception:  # pragma: no cover
                pass
            return {"success": False, "error": "任务不存在"}
        return {"success": True, "task": task}

    def get_status(self, task_id: str) -> Dict[str, Any]:
        """P1 修复 (AC2)：统一状态查询接口，返回 task / result / report 三段。

        之前调用方需要分别 get(task_id) + 自行判 status，统一接口保证：
        1) 锁内一次性取快照，避免竞态
        2) 返回结构固定：{success, task_id, status, group_name, result, report, error}
        3) 时间字段用 ISO 格式而非 strftime，方便前端解析
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"success": False, "task_id": task_id, "status": "not_found"}
            # 浅拷贝防外部修改污染内部状态
            snapshot = dict(task)
        return {
            "success": True,
            "task_id": task_id,
            "status": snapshot.get("status", "unknown"),
            "group_name": snapshot.get("group_name"),
            "theme": snapshot.get("theme"),
            "fmt": snapshot.get("fmt"),
            "message_count": snapshot.get("message_count"),
            "created_at": snapshot.get("created_at"),
            "completed_at": snapshot.get("completed_at"),
            "result": snapshot.get("result"),
            "report": snapshot.get("report"),
        }

    def get_pending(self) -> Dict[str, Any]:
        with self._lock:
            pending = [t for t in self._tasks.values() if t["status"] == "pending"]
        return {"success": True, "tasks": pending}

    def submit_result(self, task_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"success": False, "error": "任务不存在"}
            task["status"] = "completed"
            task["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            task["result"] = result
        # 事件总线：IDE AI 提交结果后通过 SSE 推送给前端
        try:
            self._event_bus.publish({
                "type": "ide_result_ready",
                "task_id": task_id,
                "group_name": task.get("group_name", ""),
            })
        except Exception as e:
            logger.warning("事件总线推送 ide_result_ready 失败: %s", e)
        return {"success": True, "message": f"任务 {task_id} 结果已提交"}

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task["status"] = "failed"
                task["result"] = {"error": error}
        # G4-2.1: 同步 in-flight 计数
        try:
            if REGISTRY is not None:
                REGISTRY.ide_tasks_active.set(len(self._inflight))
        except Exception:  # pragma: no cover
            pass
        # 推送信号（任务失败）
        self._emit(
            {"type": "task_failed", "task_id": task_id, "error": error}
        )
        # 事件总线：发布 task_failed 事件（SSE 订阅者）
        try:
            self._event_bus.publish({
                "type": "task_failed",
                "task_id": task_id,
                "error": error,
            })
        except Exception as e:
            logger.warning("事件总线推送失败（不影响任务失败标记）: %s", e)
        # Webhook 回调
        try:
            self._fire_webhook({
                "type": "task_failed",
                "task_id": task_id,
                "error": error,
            })
        except Exception as e:
            logger.warning("Webhook 触发失败（不影响任务失败标记）: %s", e)

    def mark_completed(
        self, task_id: str, ai_data: Dict[str, Any], report_info: Dict[str, Any]
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task["status"] = "completed"
                task["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                task["result"] = ai_data
                task["report"] = report_info
        # G4-2.1: 同步 in-flight 计数
        try:
            if REGISTRY is not None:
                REGISTRY.ide_tasks_active.set(len(self._inflight))
        except Exception:  # pragma: no cover
            pass
        # 推送信号（任务完成）— AC6 容错
        try:
            self._emit(
                {
                    "type": "task_completed",
                    "task_id": task_id,
                    "has_report": bool(report_info),
                }
            )
        except Exception as e:
            logger.warning("IDE 任务完成信号推送失败: %s", e)
        # 事件总线：发布 task_completed 事件（SSE 订阅者）
        try:
            self._event_bus.publish({
                "type": "task_completed",
                "task_id": task_id,
                "has_report": bool(report_info),
            })
        except Exception as e:
            logger.warning("事件总线推送失败（不影响任务完成标记）: %s", e)
        # Webhook 回调
        try:
            self._fire_webhook({
                "type": "task_completed",
                "task_id": task_id,
            })
        except Exception as e:
            logger.warning("Webhook 触发失败（不影响任务完成标记）: %s", e)

    def set_ga(self, ga) -> None:
        """设置 GA 引用（用于读取 webhook_url 配置）。"""
        self._ga_ref = ga

    def _fire_webhook(self, event: Dict[str, Any]) -> None:
        """当配置了 webhook_url 时，POST 事件到 webhook。"""
        try:
            # 从 ga.config 读取 webhook_url
            ga = getattr(self, '_ga_ref', None)
            if ga is None:
                return
            webhook_url = ga.config.get("ide", {}).get("webhook_url", "")
            if not webhook_url:
                return
            import threading

            import httpx
            def _post():
                try:
                    httpx.post(webhook_url, json=event, timeout=5)
                except Exception as e:
                    logger.warning("Webhook 回调失败 (%s): %s", webhook_url, e)
            threading.Thread(target=_post, daemon=True).start()
        except Exception as e:
            logger.warning("Webhook 触发失败: %s", e)

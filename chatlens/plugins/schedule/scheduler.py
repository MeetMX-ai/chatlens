import json
import os
import logging
import time
import uuid
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger("chatlens.scheduler")


class TaskScheduler:
    def __init__(self, schedule_file: str):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._timer: Optional[threading.Timer] = None
        self._schedule_file = schedule_file
        self._last_cleanup_date = ""
        self._load()
        # M14：移除 self._start()，TaskScheduler 不再独立跑 60s timer；
        # 调度逻辑统一交给 ScheduleService._loop，避免双重循环。

    def shutdown(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._save()
        with self._lock:
            for task in self._tasks.values():
                if task.get("running", False):
                    task["running"] = False
                    task["status"] = "idle"
        self._save()

    def create(
        self,
        group_name: str,
        hour: int,
        minute: int,
        theme: str = "scrapbook",
        fmt: str = "jpg",
    ) -> Dict[str, Any]:
        if not group_name:
            return {"success": False, "error": "未指定群聊"}
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return {"success": False, "error": "时间格式错误"}
        task_id = str(uuid.uuid4())[:8]
        task = {
            "task_id": task_id,
            "group_name": group_name,
            "hour": hour,
            "minute": minute,
            "theme": theme,
            "fmt": fmt,
            "enabled": True,
            "running": False,
            "status": "idle",
            "last_run": "",
            "last_result": None,
            "history": [],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            self._tasks[task_id] = task
        self._save()
        logger.info(
            f"创建定时任务 {task_id}: 群聊={group_name}, 时间={hour:02d}:{minute:02d}"
        )
        return {"success": True, "task_id": task_id, "task": task}

    def list_all(self) -> Dict[str, Any]:
        with self._lock:
            tasks = []
            for t in self._tasks.values():
                task_copy = dict(t)
                task_copy.pop("running", None)
                tasks.append(task_copy)
        return {"success": True, "tasks": tasks}

    def delete(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            if task_id not in self._tasks:
                return {"success": False, "error": "任务不存在"}
            del self._tasks[task_id]
        self._save()
        return {"success": True, "message": f"已删除定时任务 {task_id}"}

    def toggle(self, task_id: str, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            if task_id not in self._tasks:
                return {"success": False, "error": "任务不存在"}
            self._tasks[task_id]["enabled"] = enabled
        self._save()
        return {
            "success": True,
            "message": f"已{'启用' if enabled else '禁用'}定时任务",
        }

    def trigger(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"success": False, "error": "任务不存在"}
            if task.get("running", False):
                return {"success": False, "error": "任务正在执行中"}
            task["running"] = True
            task["status"] = "running"
        return {"success": True, "message": "已手动触发定时任务"}

    def mark_completed(self, task_id: str, result: Dict[str, Any]) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t.get("running", False):
                t["running"] = False
                t["status"] = "completed" if result.get("success") else "failed"
                t["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                t["last_result"] = result
                self._append_history(task_id, result)
                self._save()

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t.get("running", False):
                t["running"] = False
                t["status"] = "failed"
                t["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                t["last_result"] = {"success": False, "error": error}
                self._append_history(task_id, t["last_result"])
                self._save()

    def mark_timeout(self, task_id: str) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t.get("running", False):
                t["running"] = False
                t["status"] = "timeout"
                t["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._append_history(
                    task_id, {"success": False, "error": "执行超时（10分钟）"}
                )
                self._save()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._tasks.get(task_id)

    def get_task_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    def get_due_tasks(self) -> list:
        now = time.localtime()
        now_hm = (now.tm_hour, now.tm_min)
        today_str = time.strftime("%Y-%m-%d")
        to_run = []
        with self._lock:
            for task_id, task in list(self._tasks.items()):
                if not task.get("enabled", True):
                    continue
                if task.get("running", False):
                    continue
                task_hm = (task.get("hour", 0), task.get("minute", 0))
                if task_hm == now_hm:
                    last_run_date = (task.get("last_run", "") or "")[:10]
                    if last_run_date == today_str:
                        continue
                    task["running"] = True
                    task["status"] = "running"
                    to_run.append(task_id)
        return to_run

    def should_cleanup(self) -> bool:
        today_str = time.strftime("%Y-%m-%d")
        now_hm = (time.localtime().tm_hour, time.localtime().tm_min)
        if now_hm == (3, 0) and self._last_cleanup_date != today_str:
            self._last_cleanup_date = today_str
            return True
        return False

    def cleanup_old_reports(self, reports_dir: str) -> None:
        if not os.path.exists(reports_dir):
            return
        cutoff = datetime.now() - timedelta(days=30)
        deleted_count = 0
        deleted_size = 0
        try:
            for f in os.listdir(reports_dir):
                fp = os.path.join(reports_dir, f)
                if not os.path.isfile(fp):
                    continue
                mtime = datetime.fromtimestamp(os.path.getmtime(fp))
                if mtime < cutoff:
                    size = os.path.getsize(fp)
                    os.remove(fp)
                    deleted_count += 1
                    deleted_size += size
                    logger.info(f"清理过期报告: {f} ({size / 1024:.1f}KB)")
        except OSError as e:
            logger.error(f"清理过期报告失败: {e}")
        if deleted_count > 0:
            logger.info(
                f"共清理 {deleted_count} 个过期报告，释放 {deleted_size / 1024 / 1024:.2f}MB 空间"
            )

    def _append_history(self, task_id: str, result: dict) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        history = task.setdefault("history", [])
        entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "success": result.get("success", False),
            "method": result.get("method", ""),
            "error": result.get("error", ""),
        }
        history.insert(0, entry)
        task["history"] = history[:20]

    def _load(self) -> None:
        if not os.path.exists(self._schedule_file):
            return
        try:
            with open(self._schedule_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                for task_id, task in data.items():
                    task["running"] = False
                    if task.get("status") == "running":
                        task["status"] = "idle"
                    self._tasks[task_id] = task
            logger.info(f"已加载 {len(data)} 个定时任务")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"加载定时任务失败: {e}")

    def _save(self) -> None:
        try:
            schedule_dir = os.path.dirname(self._schedule_file)
            os.makedirs(schedule_dir, exist_ok=True)
            with self._lock:
                data = {}
                for task_id, task in self._tasks.items():
                    task_copy = {k: v for k, v in task.items() if k != "running"}
                    data[task_id] = task_copy
                with open(self._schedule_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"保存定时任务失败: {e}")

    # M14：_start / _tick 已删除 — TaskScheduler 不再独立跑 timer，
    # 60s 调度循环统一由 ScheduleService._loop 驱动（避免双重循环）。

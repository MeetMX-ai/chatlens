import csv
import io
import json
import logging
import os
import time
import asyncio
import threading
from concurrent.futures import Future
from typing import Dict, Any, List

from chatlens._defaults import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

from chatlens.core._chatlog_runtime import get_start_time, run_chatlog_decrypt
from .ide_tasks import IDETaskQueue
from .analysis_orchestrator import AnalysisOrchestrator, is_ollama_available_sync

logger = logging.getLogger("chatlens.plugins.web")


class WebService:
    """Web 服务门面类 — 组合编排器、IDE 任务、定时任务等子模块"""

    API_DOCS: List[Dict[str, Any]] = []
    # 列表接口内存缓存（短 TTL，避免冷启动 / 短时间内重复打 DB）
    _LIST_CACHE_TTL = 5.0  # 秒
    # H2 修复：状态详情（chatlog 联系人 / 报告统计）30s 内存缓存
    _STATUS_DETAIL_CACHE_TTL = 30.0  # 秒

    def __init__(self, ga: Any) -> None:
        self.ga = ga
        self.config: Dict[str, Any] = ga.config
        self.ide_tasks = IDETaskQueue()
        self.ide_tasks.set_ga(ga)
        self.orchestrator = AnalysisOrchestrator(ga)
        self._start_time = get_start_time()
        self._error_count = 0
        # 列表响应缓存: {key: (payload, timestamp)}
        self._list_cache: Dict[str, Any] = {}
        # M20: singleflight — 同 key 并发时只有一个线程跑 producer，其余等 Future
        self._list_inflight: Dict[str, Future] = {}
        self._list_inflight_lock = threading.Lock()
        # H2 修复：状态详情缓存: {key: (payload, timestamp)}
        self._status_detail_cache: Dict[str, Any] = {}

    def _invalidate_list_cache(self, key: str) -> None:
        """数据写操作后失效对应缓存"""
        self._list_cache.pop(key, None)

    def _invalidate_status_cache(self) -> None:
        """H2 修复：数据写操作后失效状态详情缓存（chatlog/报告统计）"""
        self._status_detail_cache.clear()

    def _cached_list(self, key: str, producer):
        """M20: 短 TTL 内存缓存 + singleflight，避免缓存过期瞬间 N 并发都打 producer。"""
        now = time.time()
        entry = self._list_cache.get(key)
        if entry is not None and now - entry[1] < self._LIST_CACHE_TTL:
            return entry[0]
        with self._list_inflight_lock:
            fut = self._list_inflight.get(key)
            if fut is not None:
                return fut.result()
            fut = Future()
            self._list_inflight[key] = fut
        try:
            payload = producer()
            self._list_cache[key] = (payload, time.time())
            fut.set_result(payload)
            return payload
        except Exception as exc:
            fut.set_exception(exc)
            raise
        finally:
            self._list_inflight.pop(key, None)

    def get_api_docs(self) -> List[Dict[str, Any]]:
        from ._shared_docs import get_report_api_docs

        web_docs = [
            {
                "path": "/api/status",
                "method": "GET",
                "description": "获取系统状态",
                "parameters": [],
            },
            {
                "path": "/api/groups",
                "method": "GET",
                "description": "获取所有群聊列表",
                "parameters": [],
            },
            {
                "path": "/api/health",
                "method": "GET",
                "description": "健康检查",
                "parameters": [],
            },
            {
                "path": "/api/analysis/stats",
                "method": "GET",
                "description": "获取群聊统计数据",
                "parameters": [{"name": "group", "type": "string", "required": True}],
            },
            {
                "path": "/api/analysis/auto",
                "method": "POST",
                "description": "自动分析",
                "parameters": [
                    {"name": "group_name", "type": "string", "required": True}
                ],
            },
            {
                "path": "/api/data/delete",
                "method": "DELETE",
                "description": "删除群聊数据",
                "parameters": [
                    {"name": "group_name", "type": "string", "required": True}
                ],
            },
        ]
        return web_docs + get_report_api_docs()  # type: ignore[return-value]

    def _get_wechat_bridge(self) -> Any:
        p = self.ga.get_provider("wechat")
        return p.bridge if p else None

    # ── 状态 / 健康 ──────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """H2 修复：只返回轻量字段，避免首屏必打 SQLite + reports 全扫。
        `chatlog_available` 是 `wechat.is_available()` boolean check，无 IO 开销，保留供 UI 渲染状态条。"""
        ai_cfg = self.config.get("ai_service", {})
        wechat = self.ga.get_provider("wechat")
        chatlog_available = wechat.is_available() if wechat else False
        return {
            "api_key_configured": self.ga.has_api_key(),
            "ollama_available": self._check_ollama_available(),
            "ide_available": bool(self.ide_tasks) and self.ide_tasks.has_idle_worker()
            if hasattr(self.ide_tasks, "has_idle_worker")
            else bool(self.ide_tasks),
            "error_count": self._error_count,
            "chatlog_available": chatlog_available,  # UI 状态条依赖（H2 修复后保留）
            # 保留 ai_provider/ai_key_placeholder 等轻量展示字段（用于 UI 渲染）
            "ai_provider": ai_cfg.get("provider", ""),
            "ai_key_placeholder": self.ga.is_api_key_placeholder(),
            "ai_configured": self.ga.has_api_key(),
        }

    def _check_ollama_available(self) -> bool:
        """轻量检查 ollama 是否可用（不阻塞首屏；与 M2 共享 5s 缓存 + httpx 单例）"""
        return is_ollama_available_sync()

    def _compute_status_details(self) -> Dict[str, Any]:
        """H2 修复：重状态字段拆出来，用 30s 内存缓存 + os.scandir 替代 os.listdir+getsize。"""
        now = time.time()
        entry = self._status_detail_cache.get("details")
        if entry is not None:
            payload, ts = entry
            if now - ts < self._STATUS_DETAIL_CACHE_TTL:
                return payload

        wechat = self.ga.get_provider("wechat")
        chatlog_available = wechat.is_available() if wechat else False
        chatlog_talkers_count = 0
        if chatlog_available:
            try:
                bridge = self._get_wechat_bridge()
                chatlog_talkers_count = len(bridge.get_all_talkers()) if bridge else 0
            except (OSError, ValueError):
                pass

        reports_dir = self.ga.get_reports_dir()
        report_count = 0
        report_total_size = 0
        if os.path.exists(reports_dir):
            # 使用 os.scandir 替代 os.listdir + os.path.getsize，
            # scandir 在大多数平台直接给出 DirEntry.lstat()，免去额外 stat
            try:
                for entry in os.scandir(reports_dir):
                    if entry.is_file(follow_symlinks=False):
                        report_count += 1
                        try:
                            report_total_size += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
            except OSError:
                pass

        payload = {
            "chatlog_available": chatlog_available,
            "chatlog_talkers_count": chatlog_talkers_count,
            "groups": self.ga.get_groups(),
            "report_count": report_count,
            "report_total_size_kb": round(report_total_size / 1024, 1),
        }
        self._status_detail_cache["details"] = (payload, now)
        return payload

    def get_health(self) -> Dict[str, Any]:
        import psutil

        uptime = time.time() - self._start_time
        uptime_str = (
            f"{int(uptime // 3600)}h{int((uptime % 3600) // 60)}m{int(uptime % 60)}s"
        )
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_mb = round(mem_info.rss / 1024 / 1024, 2)
        wechat = self.ga.get_provider("wechat")
        chatlog_available = wechat.is_available() if wechat else False
        scheduled_count = 0
        if hasattr(self.ga, "schedule") and self.ga.schedule:
            scheduled_count = self.ga.schedule.get_task_count()
        return {
            "status": "ok",
            "uptime": uptime_str,
            "uptime_seconds": round(uptime, 1),
            "memory_mb": mem_mb,
            "chatlog_available": chatlog_available,
            "scheduled_tasks": scheduled_count,
            "error_count": self._error_count,
        }

    # ── 数据管理 ──────────────────────────────────────────────

    def get_groups(self) -> Dict[str, Any]:
        def _produce() -> Dict[str, Any]:
            all_groups = self.ga.get_groups()
            data_files = self.ga.get_data_files()
            file_groups = [f["group_name"] for f in data_files]
            all_groups = list(set(all_groups + file_groups))
            available = self.ga.providers.get_available()
            group_info = []
            for g in all_groups:
                display_name = g
                for p in available:
                    try:
                        dn = p.get_display_name(g)
                        if dn and dn != g:
                            display_name = dn
                            break
                    except (OSError, ValueError):
                        pass
                group_info.append({"value": g, "label": display_name})
            return {"success": True, "groups": all_groups, "group_info": group_info}

        return self._cached_list("groups", _produce)

    def get_chatlog_chatrooms(self) -> Dict[str, Any]:
        try:
            bridge = self._get_wechat_bridge()
            if not bridge:
                return {
                    "success": False,
                    "error": "微信 provider 未启用",
                    "chatrooms": [],
                }
            rooms = bridge.get_chatrooms()
            return {"success": True, "chatrooms": rooms}
        except (OSError, ValueError) as e:
            return {"success": False, "error": str(e), "chatrooms": []}

    def get_chatlog_talkers(self) -> Dict[str, Any]:
        try:
            bridge = self._get_wechat_bridge()
            if not bridge:
                return {
                    "success": False,
                    "error": "微信 provider 未启用",
                    "talkers": [],
                }
            talkers = bridge.get_all_talkers()
            return {"success": True, "talkers": talkers}
        except (OSError, ValueError) as e:
            return {"success": False, "error": str(e), "talkers": []}

    def load_from_chatlog(self, talker: str, limit: int = 0) -> Dict[str, Any]:
        try:
            messages = self.ga.load_from_provider(talker, "wechat", limit)
            if not messages:
                return {"success": False, "error": f"未找到 {talker} 的消息"}
            self.ga.save_loaded(talker, messages)
            self.orchestrator.invalidate_cache(talker)
            self._invalidate_list_cache("groups")
            self._invalidate_list_cache("data_files")
            self._invalidate_status_cache()
            return {"success": True, "message_count": len(messages), "talker": talker}
        except (OSError, ValueError) as e:
            logger.error(f"从 chatlog 加载消息失败: {e}")
            self._error_count += 1
            return {"success": False, "error": str(e)}

    def delete_data(self, group_name: str) -> Dict[str, Any]:
        if not group_name:
            return {"success": False, "error": "未指定数据名称"}
        deleted = self.ga.delete_loaded(group_name)
        self.orchestrator.invalidate_cache(group_name)
        self._invalidate_list_cache("groups")
        self._invalidate_list_cache("data_files")
        self._invalidate_status_cache()
        if deleted:
            return {"success": True, "message": f"已删除 {group_name} 的数据"}
        return {"success": False, "error": f"未找到 {group_name} 的数据"}

    def delete_data_batch(self, group_names: List[str]) -> Dict[str, int]:
        """F7 修复：批量删除群聊数据，复用 delete_data 的删除逻辑。"""
        deleted = sum(1 for n in group_names if self.delete_data(n).get("success"))
        return {"success": True, "deleted": deleted, "failed": len(group_names) - deleted}

    def export_data(self, group_name: str, fmt: str = "csv") -> Any:
        messages = self.ga.get_messages(group_name)
        if not messages:
            return {"success": False, "error": f"未找到 {group_name} 的消息数据"}
        if fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                ["时间", "发送者", "发送者备注", "消息类型", "内容", "引用内容", "群名"]
            )
            for m in messages:
                writer.writerow(
                    [
                        m.timestamp or "",
                        m.sender or "",
                        m.sender_remark or "",
                        m.msg_type or "",
                        m.content or "",
                        m.quote_content or "",
                        m.group_name or "",
                    ]
                )
            output.seek(0)
            safe_name = group_name.replace("@chatroom", "").replace("/", "_")
            return (
                output.getvalue().encode("utf-8-sig"),
                f"{safe_name}.csv",
                "text/csv; charset=utf-8",
            )
        elif fmt == "json":
            data = {
                "group_name": group_name,
                "message_count": len(messages),
                "messages": [
                    m.to_dict()
                    if hasattr(m, "to_dict")
                    else {
                        "sender": m.sender,
                        "content": m.content,
                        "msg_type": m.msg_type,
                        "timestamp": m.timestamp,
                        "sender_remark": m.sender_remark,
                    }
                    for m in messages
                ],
            }
            json_output = json.dumps(data, ensure_ascii=False, indent=2)
            safe_name = group_name.replace("@chatroom", "").replace("/", "_")
            return (
                json_output.encode("utf-8"),
                f"{safe_name}.json",
                "application/json; charset=utf-8",
            )
        return {"success": False, "error": f"不支持的导出格式: {fmt}"}

    def get_data_files(self) -> Dict[str, Any]:
        def _produce() -> Dict[str, Any]:
            return {"success": True, "files": self.ga.get_data_files()}

        return self._cached_list("data_files", _produce)

    def refresh_chatlog(self) -> Dict[str, Any]:
        success = run_chatlog_decrypt()
        if success:
            wechat = self.ga.get_provider("wechat")
            if wechat:
                wechat.reset_connections()
            return {"success": True, "message": "数据库已刷新"}
        return {"success": False, "error": "解密刷新失败，请确认微信正在运行"}

    # ── 分析（委托给 orchestrator）────────────────────────────

    def get_stats(self, group_name: str) -> Dict[str, Any]:
        return self.orchestrator.get_stats(group_name)

    def get_ai_analysis(
        self,
        group_name: str,
        use_rules: bool = False,
        start_date: str = "",
        end_date: str = "",
        theme: str = "scrapbook",
        fmt: str = "jpg",
        skip_report: bool = False,
        use_ide: bool = False,
    ) -> Dict[str, Any]:
        return self.orchestrator.get_ai_analysis(
            group_name, use_rules, start_date, end_date, theme, fmt, skip_report, use_ide
        )

    def auto_analyze(
        self,
        group_name: str,
        theme: str = "scrapbook",
        fmt: str = "jpg",
        start_date: str = "",
        end_date: str = "",
        use_ide: bool = False,
        use_rules: bool = False,
        use_fallback: bool = False,
    ) -> Dict[str, Any]:
        return self.orchestrator.auto_analyze(
            group_name, theme, fmt, start_date, end_date,
            ide_tasks=self.ide_tasks, use_ide=use_ide, use_rules=use_rules,
            use_fallback=use_fallback,
        )

    def get_daily_dates(self, group_name: str) -> Dict[str, Any]:
        messages = self.ga.get_messages(group_name)
        if not messages:
            return {"success": True, "dates": [], "date_stats": []}
        date_map: Dict[str, dict] = {}
        for msg in messages:
            ts = getattr(msg, "timestamp", "") or ""
            if not ts:
                continue
            day = ts[:10]
            if day not in date_map:
                date_map[day] = {"date": day, "count": 0, "members": []}
            date_map[day]["count"] += 1
            sender = (
                getattr(msg, "sender_remark", "") or getattr(msg, "sender", "") or ""
            )
            if sender and sender not in date_map[day]["members"]:
                date_map[day]["members"].append(sender)
        date_stats = [
            {"date": v["date"], "count": v["count"], "members": len(v["members"])}
            for v in date_map.values()
        ]
        date_stats.sort(key=lambda x: x["date"], reverse=True)
        dates = [d["date"] for d in date_stats]
        return {"success": True, "dates": dates, "date_stats": date_stats}

    def get_daily_analysis(self, group_name: str, date: str) -> Dict[str, Any]:
        return self.orchestrator.get_daily_analysis(group_name, date)

    def compare_groups(self, group_names: list) -> Dict[str, Any]:
        return self.orchestrator.compare_groups(group_names)

    def daily_auto_report(
        self, group_name: str, date: str, theme: str = "scrapbook", fmt: str = "jpg"
    ) -> Dict[str, Any]:
        daily_result = self.get_daily_analysis(group_name, date)
        if not daily_result.get("message_count"):
            return {"success": False, "error": f"{date} 没有消息数据"}
        report_result = self.ga.report.generate_image(
            group_name=group_name,
            stats=daily_result["stats"],
            ai_data=daily_result["ai_data"],
            theme=theme,
            fmt=fmt,
            generate_image=True,
        )
        if asyncio.iscoroutine(report_result):
            report_result = asyncio.run(report_result)
        if not report_result.get("success"):
            return {
                "success": False,
                "error": report_result.get("error", "报告生成失败"),
            }
        report_info = report_result.get("report", {})
        return {
            "success": True,
            "date": date,
            "message_count": daily_result["message_count"],
            "report": report_info,
        }

    # ── IDE 任务 ──────────────────────────────────────────────

    def get_ide_prompt(self, group_name: str) -> Dict[str, Any]:
        messages = self.ga.get_messages(group_name)
        if not messages:
            return {"success": False, "error": "没有可分析的消息"}
        try:
            from chatlens.core.ai_analyzer import generate_ide_prompt

            prompt = generate_ide_prompt(group_name, messages)
            return {"success": True, "prompt": prompt}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_ide_task(
        self,
        group_name: str,
        theme: str = "scrapbook",
        fmt: str = "jpg",
        start_date: str = "",
        end_date: str = "",
    ) -> Dict[str, Any]:
        messages = self.orchestrator.filter_messages(group_name, start_date, end_date)
        if not messages:
            return {"success": False, "error": "没有可分析的消息"}
        result = self.ide_tasks.create(group_name, theme, fmt, len(messages))
        if not result.get("success"):
            return result
        task_id = result["task_id"]
        t = threading.Thread(
            target=self.orchestrator.run_ide_analysis,
            args=(messages, self.ga, self.ide_tasks, task_id, group_name, theme, fmt),
            daemon=True,
        )
        t.start()
        # 4.1 (AC1.3): 追踪 in-flight 线程（graceful shutdown 时 join）
        try:
            self.ide_tasks.track(t)
        except Exception:
            pass
        return result

    def get_ide_task(self, task_id: str) -> Dict[str, Any]:
        return self.ide_tasks.get(task_id)

    def get_ide_pending_tasks(self) -> Dict[str, Any]:
        return self.ide_tasks.get_pending()

    def submit_ide_result(self, task_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        task_data = self.ide_tasks.get(task_id)
        if not task_data.get("success"):
            return task_data
        task = task_data["task"]
        group_name = task.get("group_name", "")
        theme = task.get("theme", "scrapbook")
        fmt = task.get("fmt", "jpg")
        report_info = {}
        if group_name:
            try:
                report_info = self.orchestrator.generate_report(
                    group_name, result, theme, fmt
                )
            except Exception as e:
                logger.warning(f"IDE 任务报告生成失败: {e}")
        self.ide_tasks.mark_completed(task_id, result, report_info)
        # 报告写盘后失效状态缓存
        self._invalidate_status_cache()
        return {"success": True, "message": f"任务 {task_id} 结果已提交并生成报告"}

    # ── 定时任务 ──────────────────────────────────────────────

    def create_scheduled_task(
        self,
        group_name: str,
        hour: int,
        minute: int,
        theme: str = "scrapbook",
        fmt: str = "jpg",
    ) -> Dict[str, Any]:
        if not hasattr(self.ga, "schedule") or not self.ga.schedule:
            return {"success": False, "error": "Schedule 插件未启用"}
        return self.ga.schedule.create(group_name, hour, minute, theme, fmt)  # type: ignore[no-any-return]

    def list_scheduled_tasks(self) -> Dict[str, Any]:
        if not hasattr(self.ga, "schedule") or not self.ga.schedule:
            return {"success": False, "tasks": [], "error": "Schedule 插件未启用"}
        return self.ga.schedule.list_all()  # type: ignore[no-any-return]

    def delete_scheduled_task(self, task_id: str) -> Dict[str, Any]:
        if not hasattr(self.ga, "schedule") or not self.ga.schedule:
            return {"success": False, "error": "Schedule 插件未启用"}
        return self.ga.schedule.delete(task_id)  # type: ignore[no-any-return]

    def toggle_scheduled_task(self, task_id: str, enabled: bool) -> Dict[str, Any]:
        if not hasattr(self.ga, "schedule") or not self.ga.schedule:
            return {"success": False, "error": "Schedule 插件未启用"}
        return self.ga.schedule.toggle(task_id, enabled)  # type: ignore[no-any-return]

    def trigger_scheduled_task(self, task_id: str) -> Dict[str, Any]:
        if not hasattr(self.ga, "schedule") or not self.ga.schedule:
            return {"success": False, "error": "Schedule 插件未启用"}
        return self.ga.schedule.trigger(task_id)  # type: ignore[no-any-return]

    # ── 配置管理 ──────────────────────────────────────────────

    def get_config(self) -> Dict[str, Any]:
        ai_cfg = self.config.get("ai_service", {})
        masked_key = ""
        is_placeholder = False
        if ai_cfg.get("api_key"):
            key = ai_cfg["api_key"]
            if key.strip() in ("YOUR_API_KEY_HERE", "YOUR_API_KEY", "PLACEHOLDER"):
                is_placeholder = True
                masked_key = ""
            elif len(key) > 6:
                masked_key = key[:3] + "***" + key[-3:]
            else:
                masked_key = "***"
        return {
            "success": True,
            "config": {
                "ai_service": {
                    "provider": ai_cfg.get("provider", "deepseek"),
                    "api_key": masked_key,
                    "api_key_set": self.ga.has_api_key(),
                    "api_key_placeholder": is_placeholder,
                    "base_url": ai_cfg.get("base_url", "https://api.deepseek.com/v1"),
                    "model": ai_cfg.get("model", "deepseek-chat"),
                    "temperature": ai_cfg.get("temperature", 0.7),
                    "max_tokens": ai_cfg.get("max_tokens", 4096),
                    "concurrent_workers": int(
                        ai_cfg.get("concurrent_workers", 5)
                    ),
                    "enable_thinking": bool(ai_cfg.get("enable_thinking", True)),
                },
                "server": self.config.get(
                    "server", {"host": DEFAULT_SERVER_HOST, "port": DEFAULT_SERVER_PORT}
                ),
            },
        }

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """深度合并 override 到 base，返回合并后的新字典"""
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = WebService._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _validate_config(config: dict) -> list[str]:
        """验证配置项，返回错误列表"""
        errors = []

        # 验证 server 配置
        server = config.get("server", {})
        if "port" in server:
            port = server["port"]
            if not isinstance(port, int) or port < 1 or port > 65535:
                errors.append("server.port 必须是 1-65535 之间的整数")

        if "host" in server:
            host = server["host"]
            if not isinstance(host, str) or not host.strip():
                errors.append("server.host 不能为空")

        # 验证 AI 配置
        ai = config.get("ai_service", {})
        if "api_key" in ai and ai["api_key"]:
            key = ai["api_key"]
            if not isinstance(key, str) or len(key) < 10:
                errors.append("ai_service.api_key 格式无效")
        # 验证 concurrent_workers（1-5 整数）
        cw = ai.get("concurrent_workers", None)
        if cw is not None:
            try:
                cw_i = int(cw)
            except (TypeError, ValueError):
                errors.append("ai_service.concurrent_workers 必须是整数")
                cw_i = None
            if cw_i is not None and not (1 <= cw_i <= 5):
                errors.append("ai_service.concurrent_workers 必须在 1-5 之间（1=串行, 2-5=并发数）")

        return errors

    def save_config(self, new_config: dict) -> Dict[str, Any]:
        errors = self._validate_config(new_config)
        if errors:
            return {"success": False, "error": "; ".join(errors)}
        ai_cfg = new_config.get("ai_service", {})
        # Bug1 修复：前端把 api_key 留空时会提交 "" 或 "***..."，都应视为"未修改"，
        # 否则会被 _deep_merge 覆盖为 "" 永久清空原 key
        try:
            api_key_raw = ai_cfg.get("api_key")
            if api_key_raw is not None:
                stripped = api_key_raw.strip() if isinstance(api_key_raw, str) else ""
                if stripped == "" or stripped.startswith("***"):
                    ai_cfg.pop("api_key", None)
        except Exception:
            logger.warning("处理 api_key 时出错，保留原值", exc_info=True)
        # 深度合并新配置到现有配置
        self.config = self._deep_merge(self.config, new_config)
        # 任何 ai_service 字段（含 concurrent_workers）变化都重新构造 analyzer
        from chatlens.core.ai_analyzer import GroupAIAnalyzer

        self.ga.ai_analyzer = GroupAIAnalyzer(self.config.get("ai_service", {}))
        # 配置写入两个位置保持一致：chatlens 启动入口（main.py:114）读的是
        # 项目根目录的 config/config.json；而 API 路径位于 chatlens/plugins/web/。
        # 两边都写，避免重启后读取到旧配置。
        here = os.path.dirname(__file__)
        candidates = [
            os.path.abspath(os.path.join(here, "..", "..", "config", "config.json")),
            os.path.abspath(os.path.join(here, "..", "..", "..", "config", "config.json")),
        ]
        for config_path in candidates:
            try:
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
            except OSError as e:
                logger.warning("写配置 %s 失败: %s", config_path, e)
        return {"success": True, "message": "配置已保存"}

    # ── 报告 / 生命周期 ──────────────────────────────────────

    def generate_report(self, group_name: str) -> Dict[str, Any]:
        stats_data = self.get_stats(group_name)
        if not stats_data.get("success"):
            return stats_data
        result = {"group_name": group_name, **stats_data["data"]}
        ai_data = self.get_ai_analysis(group_name)
        if ai_data.get("success"):
            result["ai_data"] = ai_data["data"]
        else:
            result["ai_data"] = {}
        return {"success": True, "data": result}

    def shutdown(self) -> None:
        for p in self.ga.providers.get_all():
            try:
                p.reset_connections()
            except Exception:
                logger.warning(f"重置 provider {p.name} 连接失败", exc_info=True)
        if hasattr(self.ga, "schedule") and self.ga.schedule:
            self.ga.schedule.shutdown()

"""分析编排器 — 负责分析方法的调度（AI / Ollama / 规则 / IDE）"""

import asyncio
import copy
import json
import logging
import os
import secrets
import threading
import time
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import quote

import httpx

from chatlens.core.ai_analyzer import rule_based_analysis
from chatlens.core._analysis_data import EMPTY_RESULT
from chatlens.errors import (
    AIError,
    ChatLensError,
    ChatlogError,
    ConfigError,
    ReportError,
)

logger = logging.getLogger("chatlens.analysis_orchestrator")

_CACHE_TTL = 300  # 缓存有效期（秒）
_REPORT_CACHE_TTL = 300  # 报告缓存有效期（秒）
_OLLAMA_URL = "http://localhost:11434/api/tags"
_OLLAMA_TIMEOUT = 0.5  # 秒
_OLLAMA_CACHE_TTL = 5.0  # 秒

# M2+M3 共享：Ollama 可用性缓存，key="available" → (timestamp, bool)
_ollama_cache: Dict[str, Tuple[float, bool]] = {}
_ollama_client: Optional[httpx.AsyncClient] = None


def _get_ollama_client() -> httpx.AsyncClient:
    """模块级 httpx.AsyncClient 单例，避免每次新建连接"""
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT)
    return _ollama_client


async def _check_ollama_available_async() -> bool:
    """异步探测 Ollama 是否可用，命中 5s 缓存则不发请求。"""
    now = time.time()
    cached = _ollama_cache.get("available")
    if cached is not None:
        ts, val = cached
        if now - ts < _OLLAMA_CACHE_TTL:
            return val
    try:
        client = _get_ollama_client()
        resp = await asyncio.wait_for(client.get(_OLLAMA_URL), timeout=_OLLAMA_TIMEOUT)
        available = resp.status_code == 200
    except Exception:
        available = False
    _ollama_cache["available"] = (now, available)
    return available


def is_ollama_available_sync() -> bool:
    """同步入口：M2（orchestrator 内部）+ M3（api_server）共用，共享 5s 缓存。
    用 new_event_loop + run_until_complete + close 而不是 asyncio.run()，
    避免破坏调用线程的全局事件循环状态（与测试套件的 asyncio.get_event_loop() 兼容）。"""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_check_ollama_available_async())
        finally:
            loop.close()
    except Exception:
        return False


async def _fetch_ollama_models_async() -> Optional[Dict[str, Any]]:
    """异步拉取 Ollama /api/tags 返回的模型列表（仅在已知可用时调用）。"""
    try:
        client = _get_ollama_client()
        resp = await asyncio.wait_for(client.get(_OLLAMA_URL), timeout=_OLLAMA_TIMEOUT)
        if resp.status_code != 200:
            return None
        return json.loads(resp.text)
    except Exception:
        return None


class AnalysisOrchestrator:
    """编排分析流程：AI → Ollama → 规则 → IDE，按优先级降级"""

    def __init__(self, ga) -> None:
        self.ga = ga
        self._stats_cache: Dict[str, Any] = {}
        self._stats_cache_time: Dict[str, float] = {}
        self._rule_cache: Dict[str, Any] = {}
        self._rule_cache_time: Dict[str, float] = {}
        # H1 修复：/api/analysis/ai 的 5min 内存缓存
        # key: (group_name, use_ide, use_rules, start_date, end_date, format)
        self._ai_cache: Dict[tuple, Dict[str, Any]] = {}
        # 优化 3 (AC3)：报告生成缓存，避免重复跑 Chrome
        # key: (group_name, theme, fmt, ai_data_md5)
        self._report_cache: Dict[tuple, tuple] = {}  # (payload, ts)

    def get_stats(self, group_name: str, start_date: str = "", end_date: str = "") -> Dict[str, Any]:
        now = time.time()
        # 清理过期缓存
        expired = [k for k, t in self._stats_cache_time.items() if now - t > _CACHE_TTL]
        for k in expired:
            self._stats_cache.pop(k, None)
            self._stats_cache_time.pop(k, None)
        cache_key = (group_name, start_date, end_date)
        if cache_key in self._stats_cache:
            cached_at = self._stats_cache_time.get(cache_key, 0)
            if now - cached_at < _CACHE_TTL:
                return {"success": True, "data": self._stats_cache[cache_key]}
        messages = self.ga.get_messages(group_name)
        # Apply date range filter
        if start_date or end_date:
            filtered = []
            for m in (messages or []):
                ts = (getattr(m, "timestamp", "") or "")[:10]
                if start_date and ts < start_date:
                    continue
                if end_date and ts > end_date:
                    continue
                filtered.append(m)
            messages = filtered
        result = self.ga.stats_analyzer.analyze(messages or [])
        result = self._normalize_stats(result)
        self._stats_cache[cache_key] = result
        self._stats_cache_time[cache_key] = now
        return {"success": True, "data": result}

    def invalidate_cache(self, group_name: str) -> None:
        # _stats_cache keys are now tuples (group_name, start_date, end_date)
        stale_stats = [k for k in self._stats_cache if (k[0] if isinstance(k, tuple) else k) == group_name]
        for k in stale_stats:
            self._stats_cache.pop(k, None)
            self._stats_cache_time.pop(k, None)
        self._rule_cache.pop(group_name, None)
        self._rule_cache_time.pop(group_name, None)
        # 同时清掉该群相关的 ai 缓存
        stale_keys = [k for k in self._ai_cache if k[0] == group_name]
        for k in stale_keys:
            self._ai_cache.pop(k, None)
        # 优化 3 (AC3)：同时清掉该群相关的报告缓存
        report_stale = [k for k in self._report_cache if k[0] == group_name]
        for k in report_stale:
            self._report_cache.pop(k, None)

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
        # H1 修复：没配置 API Key 且未启用 use_rules 时，直接返回 EMPTY_RESULT 占位，
        # 避免无谓地跑全量 jieba 分词 + 关键词打分
        if not use_rules and not self.ga.has_api_key():
            return {
                "success": True,
                "data": copy.deepcopy(EMPTY_RESULT),
                "method": "empty",
                "report": {},
            }

        # H1 修复：5min 内存缓存，避免首屏/重复请求反复跑重活
        cache_key = (group_name, use_ide, use_rules, start_date, end_date, fmt)
        now = time.time()
        cached = self._ai_cache.get(cache_key)
        if cached is not None:
            payload, ts = cached
            if now - ts < _CACHE_TTL:
                return copy.deepcopy(payload)

        messages = self._filter_messages_by_range(group_name, start_date, end_date)
        if not messages:
            return {"success": False, "error": "没有可分析的消息"}
        try:
            method = "rules"
            if use_rules or not self.ga.has_api_key():
                result = rule_based_analysis(messages)
                method = "rules"
            else:
                try:
                    result = self.ga.ai_analyzer.full_analysis(messages)
                    method = "ai"
                except Exception as ai_err:
                    # AI 分析失败（如 API 限流 429），降级到规则分析
                    logger.warning("AI 分析失败，降级到规则分析: %s", ai_err)
                    result = rule_based_analysis(messages)
                    method = "rules_fallback"
            report_info: Dict[str, Any] = {}
            if not skip_report:
                report_info = self._generate_report(group_name, result, theme, fmt, start_date, end_date)
            payload = {
                "success": True,
                "data": result,
                "method": method,
                "report": report_info,
            }
            self._ai_cache[cache_key] = (payload, now)
            return copy.deepcopy(payload)
        except Exception as e:
            # 保留 dict 返回以兼容 web 层调用方契约
            logger.exception("AI 分析失败: %s", e)
            return {"success": False, "error": str(e)}

    def auto_analyze(
        self,
        group_name: str,
        theme: str = "scrapbook",
        fmt: str = "jpg",
        start_date: str = "",
        end_date: str = "",
        ide_tasks=None,
        use_ide: bool = False,
        use_rules: bool = False,
        use_fallback: bool = False,
    ) -> Dict[str, Any]:
        """自动分析调度：
        - use_ide=True：仅创建 pending 任务，**不启动** daemon 线程（由 IDE 客户端接管）。
        - use_rules=True：直接走规则分析。
        - use_fallback=True：后端自跑降级链（Ollama → 规则），不调 IDE、不调 API。
        - 默认：API Key → Ollama → 规则。
        """
        messages = self._filter_messages_by_range(group_name, start_date, end_date)
        if not messages:
            return {"success": False, "error": "没有可分析的消息"}
        is_placeholder = self.ga.is_api_key_placeholder()
        try:
            # IDE 模式：秒出规则分析 + 异步 IDE AI 增强
            # 1) 同步：创建 pending 任务 + 立即用规则分析生成结果返回
            #    （前端秒出体验，不用干等 IDE）
            # 2) 异步：IDE AI 拿到任务后做深度分析，通过 submit_result 覆盖
            if use_ide and ide_tasks:
                ide_result = ide_tasks.create(
                    group_name, theme, fmt, len(messages)
                )
                if ide_result.get("success"):
                    task_id = ide_result["task_id"]
                    # 立即跑规则分析作为兜底结果（前端秒出）
                    try:
                        ai_data = rule_based_analysis(messages)
                        # 即使实际走 rules 也保留 method="ide" 标识，
                        # 让前端知道结果来自 IDE 模式（IDE 任务由 IDE 客户端进一步覆盖）
                        method = "ide"
                        stats_result = self.get_stats(group_name, start_date, end_date)
                        if stats_result.get("success"):
                            report_info = self._generate_report(
                                group_name, ai_data, theme, fmt, start_date, end_date
                            )
                        else:
                            report_info = {}
                        return {
                            "success": True,
                            "method": method,
                            "task_id": task_id,
                            "data": ai_data,
                            "report": report_info,
                            "message": "规则分析已返回，IDE AI 增强中...",
                        }
                    except Exception as e:
                        logger.warning("IDE 模式规则兜底失败: %s", e)
                        return {
                            "success": True,
                            "method": "ide",
                            "task_id": task_id,
                            "message": "已提交给 IDE AI 分析，请在 IDE 中查看结果",
                            "report": None,
                        }
                return {"success": False, "error": "IDE AI 任务创建失败"}

            # 规则模式：直接使用规则分析
            if use_rules:
                ai_data = rule_based_analysis(messages)
                method = "rules"
                stats_result = self.get_stats(group_name, start_date, end_date)
                if not stats_result.get("success"):
                    return stats_result
                report_info = self._generate_report(group_name, ai_data, theme, fmt, start_date, end_date)
                return {
                    "success": True,
                    "method": method,
                    "data": ai_data,
                    "report": report_info,
                }

            # 后端自跑降级模式：Ollama → 规则（不调 IDE、不调 API Key）
            if use_fallback:
                ai_data: Dict[str, Any] = {}
                method = "rules"
                ollama_data = self._try_ollama_analysis(messages)
                if ollama_data:
                    ai_data = ollama_data
                    method = "ollama"
                else:
                    if is_placeholder:
                        return {
                            "success": False,
                            "error": "API Key 未配置，请前往设置页面配置 API Key 后再使用 AI 分析",
                            "error_code": "API_KEY_NOT_CONFIGURED",
                        }
                    ai_data = rule_based_analysis(messages)
                    method = "rules"
                stats_result = self.get_stats(group_name, start_date, end_date)
                if not stats_result.get("success"):
                    return stats_result
                report_info = self._generate_report(group_name, ai_data, theme, fmt, start_date, end_date)
                return {
                    "success": True,
                    "method": method,
                    "data": ai_data,
                    "report": report_info,
                }

            # 自动模式：API Key → Ollama → 规则
            ai_data: Dict[str, Any] = {}
            method = "rules"
            if self.ga.has_api_key():
                try:
                    result = self.ga.ai_analyzer.full_analysis(messages)
                    ai_data = result
                    method = "ai"
                except Exception as e:
                    # AC6：logger.exception 带 stacktrace（替代 logger.warning）
                    logger.exception("AI 分析失败，降级为规则分析: %s", e)
            has_content = (
                ai_data.get("summary", {}).get("summary")
                or ai_data.get("user_titles", {}).get("user_titles")
                or ai_data.get("golden_quotes", {}).get("golden_quotes")
            )
            if method == "rules" or (method == "ai" and not has_content):
                ollama_data = self._try_ollama_analysis(messages)
                if ollama_data:
                    ai_data = ollama_data
                    method = "ollama"
                else:
                    if is_placeholder:
                        return {
                            "success": False,
                            "error": "API Key 未配置，请前往设置页面配置 API Key 后再使用 AI 分析",
                            "error_code": "API_KEY_NOT_CONFIGURED",
                        }
                    ai_data = rule_based_analysis(messages)
                    method = "rules"
            stats_result = self.get_stats(group_name, start_date, end_date)
            if not stats_result.get("success"):
                return stats_result
            report_info = self._generate_report(group_name, ai_data, theme, fmt, start_date, end_date)
            return {
                "success": True,
                "method": method,
                "data": ai_data,
                "report": report_info,
            }
        except Exception as e:
            # AC6：外层兜底改 logger.exception（带 stacktrace）+ 抛 AIError
            logger.exception("自动分析失败: %s", e)
            # AC6：抛 AIError 供 chatlens_error_handler 转 500
            raise AIError(
                f"自动分析失败: {e}",
                hint="请稍后重试，或检查 AI 服务可用性",
            ) from e

    def run_ide_analysis(
        self, messages, ga, ide_tasks, task_id, group_name, theme, fmt
    ) -> None:
        """在后台线程中运行 IDE 任务的分析"""
        try:
            # 修复 2：先查 _ai_cache 缓存（key 与 H1 一致），命中就跳过 AI
            cache_key = (group_name, True, False, "", "", fmt)
            cached_entry = self._ai_cache.get(cache_key)
            ai_data: Dict[str, Any] = {}
            report_info: Dict[str, Any] = {}
            if cached_entry is not None:
                payload, ts = cached_entry
                if time.time() - ts < _CACHE_TTL:
                    ai_data = copy.deepcopy(payload.get("data", {}))
                    report_info = copy.deepcopy(payload.get("report", {}))
                    ide_tasks.mark_completed(task_id, ai_data, report_info)
                    return

            if ga.has_api_key():
                try:
                    # 修复 2：afull_analysis 用 asyncio.gather 并发调 5 个 AI，
                    # 比串行 full_analysis 节省约 4 倍时间。这里通过 new_event_loop
                    # 包装保持与 daemon 线程兼容（避免污染外层事件循环状态）。
                    ai_data = self._async_full_analysis_sync(ga, messages)
                except Exception as e:
                    # AC6：logger.exception 带 stacktrace
                    logger.exception("AI 分析失败: %s", e)
                    ai_data = {}
            has_content = (
                ai_data.get("summary", {}).get("summary")
                or ai_data.get("user_titles", {}).get("user_titles")
                or ai_data.get("golden_quotes", {}).get("golden_quotes")
            )
            if not has_content:
                ollama_data = self._try_ollama_analysis(messages)
                if ollama_data:
                    ai_data = ollama_data
                else:
                    ai_data = rule_based_analysis(messages)
            report_info = self._generate_report(group_name, ai_data, theme, fmt)
            ide_tasks.mark_completed(task_id, ai_data, report_info)
        except Exception as e:
            # AC6：logger.exception 带 stacktrace
            logger.exception("IDE 任务分析失败: %s", e)
            ide_tasks.mark_failed(task_id, str(e))

    @staticmethod
    def _async_full_analysis_sync(ga, messages) -> Dict[str, Any]:
        """在同步上下文中跑 afull_analysis：用 new_event_loop + run_until_complete
        避免污染调用线程的全局事件循环状态（与 is_ollama_available_sync 同套写法）。"""
        analyzer = getattr(ga, "ai_analyzer", None)
        if analyzer is None or not hasattr(analyzer, "afull_analysis"):
            # 降级：没有异步接口就退到同步
            return analyzer.full_analysis(messages) if analyzer else {}
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(analyzer.afull_analysis(messages))
        finally:
            loop.close()

    def get_daily_analysis(self, group_name: str, date: str) -> Dict[str, Any]:
        messages = self.ga.get_messages(group_name)
        if not messages:
            return {
                "success": True,
                "date": date,
                "message_count": 0,
                "stats": {},
                "ai_data": {},
            }
        filtered = [m for m in messages if (m.timestamp or "")[:10] == date]
        if not filtered:
            return {
                "success": True,
                "date": date,
                "message_count": 0,
                "stats": {},
                "ai_data": {},
            }
        stats_result = self.ga.stats_analyzer.analyze(filtered)
        ai_data = rule_based_analysis(filtered)
        return {
            "success": True,
            "date": date,
            "message_count": len(filtered),
            "stats": stats_result,
            "ai_data": ai_data,
        }

    def compare_groups(self, group_names: List[str]) -> Dict[str, Any]:
        """多群对比分析 — 对比多个群聊的关键指标"""
        if not group_names or len(group_names) < 2:
            return {"success": False, "error": "至少需要选择 2 个群聊进行对比"}
        if len(group_names) > 6:
            return {"success": False, "error": "最多支持 6 个群聊同时对比"}
        comparisons = []
        for name in group_names:
            messages = self.ga.get_messages(name)
            if not messages:
                comparisons.append({"group_name": name, "available": False})
                continue
            # 复用 get_stats 缓存，避免重复计算统计数据
            stats_result = self.get_stats(name)
            stats = stats_result.get("data", {}) if stats_result.get("success") else {}
            ov = stats.get("overview", {})
            members = stats.get("member_stats", [])[:5]
            # 缓存 rule_based_analysis 结果
            now = time.time()
            if (
                name in self._rule_cache
                and now - self._rule_cache_time.get(name, 0) < _CACHE_TTL
            ):
                ai_data = self._rule_cache[name]
            else:
                ai_data = rule_based_analysis(messages)
                self._rule_cache[name] = ai_data
                self._rule_cache_time[name] = now
            comparisons.append(
                {
                    "group_name": name,
                    "available": True,
                    "total_messages": ov.get("total_messages", 0),
                    "total_members": ov.get("total_members", 0),
                    "avg_daily": ov.get("avg_messages_per_day", 0),
                    "time_start": (ov.get("time_range", {}).get("start", "") or "")[
                        :10
                    ],
                    "time_end": (ov.get("time_range", {}).get("end", "") or "")[:10],
                    "top_members": [
                        {"sender": m.get("sender", ""), "count": m.get("msg_count", 0)}
                        for m in members
                    ],
                    "msg_types": stats.get("msg_type_distribution", [])[:4],
                    "keywords": [
                        kw.get("word", "")
                        for kw in (
                            ai_data.get("keywords", {}).get("keywords", []) or []
                        )[:8]
                    ],
                    "vibe_dims": ai_data.get("chat_quality", {}).get("dimensions", [])[
                        :3
                    ],
                }
            )
        available = [c for c in comparisons if c.get("available")]
        if len(available) < 2:
            return {"success": False, "error": "至少需要 2 个有数据的群聊才能对比"}
        return {"success": True, "comparisons": comparisons}

    # ── 公共方法（委托内部实现）────────────────────────────

    def filter_messages(
        self, group_name: str, start_date: str = "", end_date: str = ""
    ) -> list:
        """按日期范围过滤消息（公共接口）"""
        return self._filter_messages_by_range(group_name, start_date, end_date)

    def generate_report(
        self, group_name: str, ai_data: Dict[str, Any], theme: str, fmt: str
    ) -> Dict[str, Any]:
        """生成报告（公共接口）"""
        return self._generate_report(group_name, ai_data, theme, fmt)

    # ── 内部方法 ──────────────────────────────────────────

    def _filter_messages_by_range(
        self, group_name: str, start_date: str = "", end_date: str = ""
    ) -> list:
        messages = self.ga.get_messages(group_name)
        if not messages:
            return []
        if not (start_date or end_date):
            return messages  # type: ignore[no-any-return]
        filtered = []
        for m in messages:
            ts = (getattr(m, "timestamp", "") or "")[:10]
            if start_date and ts < start_date:
                continue
            if end_date and ts > end_date:
                continue
            filtered.append(m)
        return filtered

    def _generate_report(
        self, group_name: str, ai_data: Dict[str, Any], theme: str, fmt: str, start_date: str = "", end_date: str = ""
    ) -> Dict[str, Any]:
        if not hasattr(self.ga, "report") or not self.ga.report:
            return {}
        stats_result = self.get_stats(group_name, start_date, end_date)
        if not stats_result.get("success"):
            return {}
        generate_image = fmt in ("jpg", "png")
        # 优化 3 (AC3)：基于 (group, theme, fmt, ai_data_hash) 的报告缓存，
        # 5 分钟内对相同输入直接复用报告，避免重复跑 Chrome
        cache_key = None
        try:
            import hashlib
            import json as _json
            ai_hash = hashlib.md5(
                _json.dumps(ai_data, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:12]
            cache_key = (group_name, theme, fmt, ai_hash)
            cached = self._report_cache.get(cache_key)
            if cached is not None:
                payload, ts = cached
                if time.time() - ts < _REPORT_CACHE_TTL:
                    return dict(payload)  # 浅拷贝避免外部修改污染
        except Exception:
            # 哈希失败也不影响主流程
            cache_key = None
        try:
            result = self.ga.report.generate_image(  # type: ignore[no-any-return]
                group_name=group_name,
                stats=stats_result["data"],
                ai_data=ai_data,
                theme=theme,
                fmt=fmt,
                generate_image=generate_image,
            )
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            report_payload = result.get("report", {})
            if cache_key is not None and report_payload:
                self._report_cache[cache_key] = (report_payload, time.time())
            return report_payload
        except Exception as e:
            # 修复 3：daemon 线程里 Chrome 失败时降级到 HTML 报告，
            # 避免整个 IDE 任务被 mark_failed 而前端永远拿不到结果。
            # AC6：logger.exception 带 stacktrace
            logger.exception("图片报告生成失败（%s），降级为 HTML 报告", e)
            if generate_image:
                try:
                    result = self.ga.report.generate_image(  # type: ignore[no-any-return]
                        group_name=group_name,
                        stats=stats_result["data"],
                        ai_data=ai_data,
                        theme=theme,
                        fmt="html",
                        generate_image=False,
                    )
                    if asyncio.iscoroutine(result):
                        result = asyncio.run(result)
                    return result.get("report", {})
                except Exception as e2:
                    # AC6：HTML 降级也失败时记录 stacktrace（保持返回 {} 兼容旧契约）
                    logger.exception("HTML 降级报告也失败: %s", e2)
                    return {}
            return {}

    def _try_ollama_analysis(self, messages: list) -> Optional[Dict[str, Any]]:
        # M2：先走 5s 缓存的可用性探测（同步包装），不可用直接返回
        if not is_ollama_available_sync():
            return None
        try:
            # 用 new_event_loop 包装而非 asyncio.run()，避免污染调用线程事件循环状态
            loop = asyncio.new_event_loop()
            try:
                data = loop.run_until_complete(_fetch_ollama_models_async())
            finally:
                loop.close()
        except Exception:
            return None
        if not data:
            return None
        models = [m.get("name", "") for m in data.get("models", [])]
        if not models:
            return None
        # 优先选择文本生成模型
        text_keywords = [
            "qwen",
            "llama",
            "chat",
            "gemma",
            "mistral",
            "deepseek",
            "yi",
            "phi",
        ]
        preferred = [
            m for m in models if any(kw in m.lower() for kw in text_keywords)
        ]
        model = preferred[0] if preferred else models[0]
        logger.info(f"检测到 Ollama，使用模型: {model}")
        try:
            from chatlens.core.ai_analyzer import GroupAIAnalyzer

            ollama_config = {
                "provider": "ollama",
                "api_key": "ollama",
                "base_url": "http://localhost:11434/v1",
                "model": model,
                "temperature": 0.7,
                "max_tokens": 4096,
            }
            analyzer = GroupAIAnalyzer(ollama_config)
            result = analyzer.full_analysis(messages)
            if result and any(
                result.get(k)
                for k in ["summary", "user_titles", "golden_quotes", "chat_quality"]
            ):
                return result
            return None
        except Exception as e:
            # AC6：logger.exception 带 stacktrace
            logger.exception("Ollama 分析失败: %s", e)
            return None

    @staticmethod
    def _normalize_stats(data: dict) -> dict:
        # M10：浅拷贝足够（仅新增 top-level key）— ov 取独立副本避免污染原数据
        data = dict(data)
        ov = dict(data.get("overview", {}))
        tr = ov.get("time_range", {})
        data.setdefault("top_members", data.get("member_stats", []))
        data.setdefault("message_types", data.get("msg_type_distribution", []))
        data.setdefault("keyword_frequency", data.get("keyword_cloud", []))
        data.setdefault(
            "interaction_pairs",
            data.get("interaction_analysis", {}).get("top_interactions", []),
        )
        ov.setdefault("start_date", tr.get("start", "")[:10])
        ov.setdefault("end_date", tr.get("end", "")[:10])
        ov.setdefault("avg_per_day", ov.get("avg_messages_per_day", 0))
        data["overview"] = ov
        return data


# ════════════════════════════════════════════════════════════════════════════
#  报告生成异步任务（不阻塞前端 + 实时进度）
# ════════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass, field
from typing import Any as _Any


# 报告生成阶段：进度百分比 / 阶段标题 / 阶段描述
REPORT_STAGES = [
    ("loading",     10, "加载消息",   "从 chatlog 拉取该群历史消息"),
    ("stats",       25, "统计分析",   "聚合发言 / 互动 / 时间分布"),
    ("ai",          55, "AI 分析",    "调用 LLM 生成摘要 / 关键词 / 称号 / 金句 / 质量锐评"),
    ("render",      75, "渲染 HTML",  "Jinja2 模板 + 内联 SVG 图表"),
    ("render_done", 80, "HTML 已就绪", "HTML 已生成，可预览"),
    ("screenshot",  90, "截图",       "Chrome headless 生成图片"),
    ("done",       100, "完成",       "报告已生成"),
]

# 第二步：纯截图任务阶段（用于"从已有 HTML 截图"）
SCREENSHOT_STAGES = [
    ("screenshot", 50,  "截图", "Chrome headless 生成图片"),
    ("done",      100, "完成", "图片已生成"),
]


@dataclass
class ReportTask:
    task_id: str
    group_name: str
    theme: str
    fmt: str
    use_ide: bool
    task_type: str = "report"  # "report" | "screenshot"
    stage: str = "queued"
    progress: int = 0
    message: str = "任务已入队"
    result: Optional[Dict[str, _Any]] = None  # 成功结果（image_path/html_path...）
    error: Optional[Dict[str, _Any]] = None    # 失败结果（code/message/hint/stage）
    warnings: List[Dict[str, str]] = field(default_factory=list)  # AI 部分失败时记录到 task 上
    start_date: str = ""
    end_date: str = ""
    created_at: float = field(default_factory=time.time)


# 进程内任务表。简易实现：内存 dict，进程重启清空。生产可换 Redis。
_report_tasks: Dict[str, ReportTask] = {}
_report_tasks_lock = threading.Lock()


def _publish_progress(task: ReportTask) -> None:
    """向 event_bus 推 report_progress 事件，订阅者（SSE / webhook）能立即收到。"""
    try:
        from chatlens.event_bus import get_event_bus
        payload = {
            "type": "report_progress",
            "task_id": task.task_id,
            "task_type": task.task_type,
            "stage": task.stage,
            "progress": task.progress,
            "message": task.message,
            "group_name": task.group_name,
        }
        # 终态（done / render_done / failed）时附加 result / error 字段，方便前端直接拿到
        if task.stage in ("done", "render_done") and task.result:
            payload["result"] = task.result
        if task.stage == "failed" and task.error:
            payload["error"] = task.error
        get_event_bus().publish(payload)
    except Exception as e:  # pragma: no cover
        logger.warning("推 report_progress 事件失败: %s", e)


def _update_stage(task: ReportTask, stage: str, message: Optional[str] = None) -> None:
    """更新任务阶段、百分比、消息，并立即推送。
    优先在 REPORT_STAGES 查；查不到则去 SCREENSHOT_STAGES 查（screenshot 任务）。"""
    for stages in (REPORT_STAGES, SCREENSHOT_STAGES):
        for s, pct, default_msg, _desc in stages:
            if s == stage:
                task.stage = stage
                task.progress = pct
                task.message = message or default_msg
                _publish_progress(task)
                return


def _make_task_id() -> str:
    return secrets.token_urlsafe(12)


def get_report_task(task_id: str) -> Optional[ReportTask]:
    with _report_tasks_lock:
        return _report_tasks.get(task_id)


def list_report_tasks() -> List[ReportTask]:
    with _report_tasks_lock:
        return list(_report_tasks.values())


def submit_report_image_task(
    orchestrator: "AnalysisOrchestrator",
    group_name: str,
    theme: str = "scrapbook",
    fmt: str = "jpg",
    use_ide: bool = False,
    generate_image: bool = False,
    start_date: str = "",
    end_date: str = "",
) -> str:
    """提交报告生成任务，立即返回 task_id。后台用线程执行。

    两步流程：
    - generate_image=False（默认）：只跑 4 阶段（loading/stats/ai/render），
      推 render_done 阶段后结束，让用户预览 HTML 后决定是否生成图片。
    - generate_image=True：跑完整 5 阶段（4 阶段 + screenshot），
      一次性返回 image_url（保持向后兼容）。
    """
    task_id = _make_task_id()
    task = ReportTask(
        task_id=task_id,
        group_name=group_name,
        theme=theme,
        fmt=fmt,
        use_ide=use_ide,
        task_type="report",
        start_date=start_date,
        end_date=end_date,
    )
    with _report_tasks_lock:
        _report_tasks[task_id] = task

    t = threading.Thread(
        target=_run_report_image_task,
        args=(orchestrator, task, generate_image),
        daemon=True,
        name=f"report-{task_id}",
    )
    t.start()
    return task_id


def _run_report_image_task(
    orchestrator: "AnalysisOrchestrator",
    task: ReportTask,
    generate_image: bool = False,
) -> None:
    """后台线程：执行报告生成，每完成一阶段推 SSE 进度。

    两步流程：
    - generate_image=False（默认）：跑 4 阶段（loading/stats/ai/render）后
      推 render_done 阶段并结束，task.result 含 html_path / html_url，
      等用户在前端点"生成图片"按钮再触发 SCREENSHOT 任务。
    - generate_image=True：跑完整 5 阶段（4 阶段 + screenshot）后
      推 done 阶段，task.result 含 image_path / image_url（向后兼容）。
    """
    try:
        ga = orchestrator.ga

        # 1) 加载消息
        _update_stage(task, "loading", "从 chatlog 拉取该群历史消息...")
        provider = ga.get_provider("wechat")
        display_name = provider.get_display_name(task.group_name) if provider else task.group_name
        messages = ga.get_messages(task.group_name) if hasattr(ga, "get_messages") else []
        if not messages:
            raise ChatlogError(
                f"群 {task.group_name} 没有可加载的消息（数据库可能为空）",
                hint="请确认 chatlog 服务在线，且该群有聊天记录",
                code="CHATLOG_NO_MESSAGES",
            )

        # Apply date range filter
        if task.start_date or task.end_date:
            filtered = []
            for m in messages:
                ts = (getattr(m, "timestamp", "") or "")[:10]
                if task.start_date and ts < task.start_date:
                    continue
                if task.end_date and ts > task.end_date:
                    continue
                filtered.append(m)
            messages = filtered

        # 2) 统计分析
        _update_stage(task, "stats", f"聚合 {len(messages)} 条消息...")
        stats_result = ga.stats_analyzer.analyze(messages)
        stats_result = AnalysisOrchestrator._normalize_stats(stats_result)

        # 3) AI 分析
        _update_stage(task, "ai", "调用 LLM 生成 AI 内容...")
        if not ga.has_api_key():
            ai_data = rule_based_analysis(messages)
            method = "rules"
        elif task.use_ide:
            # IDE 模式：仍用规则保证立即出结果，IDE 任务由前端另行触发
            ai_data = rule_based_analysis(messages)
            method = "rules"
        else:
            try:
                from chatlens.core.ai_analyzer import GroupAIAnalyzer
                ai = GroupAIAnalyzer(ga.config.get("ai_service", {}))
                ai_data = ai.full_analysis(messages)
                method = "ai"
            except Exception as e:
                logger.warning("AI 分析失败，降级到规则引擎: %s", e)
                ai_data = rule_based_analysis(messages)
                method = "rules"

        # AI 子分析失败时显式记录到 task.warnings，让用户看到真实情况
        # （不再用 rule_based_analysis 静默补齐）
        if method == "ai":
            _section_labels = {
                "summary": "群聊摘要",
                "topics": "讨论话题",
                "user_titles": "用户称号与人格画像",
                "golden_quotes": "金句集锦",
                "chat_quality": "聊天质量锐评",
                "keywords": "关键词云",
            }

            def _reason_for(section_data):
                """从 section_data 提取失败原因。无 __error__ 字段时给默认 reason。"""
                if isinstance(section_data, dict):
                    err = section_data.get("__error__")
                    if err:
                        return err
                return "AI 未返回该字段"

            _summary_data = ai_data.get("summary") or {}
            if not (_summary_data.get("summary") or ""):
                task.warnings.append({
                    "section": "summary",
                    "label": _section_labels["summary"],
                    "reason": _reason_for(_summary_data),
                })
            if not (_summary_data.get("topics") or []):
                task.warnings.append({
                    "section": "topics",
                    "label": _section_labels["topics"],
                    "reason": _reason_for(_summary_data),
                })
            _user_titles_data = ai_data.get("user_titles") or {}
            if not (_user_titles_data.get("user_titles") or []):
                task.warnings.append({
                    "section": "user_titles",
                    "label": _section_labels["user_titles"],
                    "reason": _reason_for(_user_titles_data),
                })
            _golden_quotes_data = ai_data.get("golden_quotes") or {}
            if not (_golden_quotes_data.get("golden_quotes") or []):
                task.warnings.append({
                    "section": "golden_quotes",
                    "label": _section_labels["golden_quotes"],
                    "reason": _reason_for(_golden_quotes_data),
                })
            _chat_quality_data = ai_data.get("chat_quality") or {}
            if not (_chat_quality_data.get("title") or ""):
                task.warnings.append({
                    "section": "chat_quality",
                    "label": _section_labels["chat_quality"],
                    "reason": _reason_for(_chat_quality_data),
                })
            _keywords_data = ai_data.get("keywords") or {}
            if not (_keywords_data.get("keywords") or []):
                task.warnings.append({
                    "section": "keywords",
                    "label": _section_labels["keywords"],
                    "reason": _reason_for(_keywords_data),
                })

            for _w in task.warnings:
                logger.warning("AI 分析 section 失败: %s — %s", _w["label"], _w["reason"])

        # 4) 渲染 HTML
        _update_stage(task, "render", "Jinja2 模板 + SVG 图表生成 HTML...")
        from chatlens.plugins.report import image_report as _image_report
        # generate_report_image 是 async，generate_image=False 时只渲染 HTML 不截图
        try:
            render_coro = _image_report.generate_report_image(
                group_name=display_name,
                stats=stats_result,
                ai_data=ai_data,
                theme=task.theme,
                fmt=task.fmt,
                generate_image=False,
            )
            if asyncio.iscoroutine(render_coro):
                _placeholder, html_path = asyncio.run(render_coro)
            else:
                _placeholder, html_path = render_coro
        except Exception as e:
            logger.exception("渲染 HTML 失败: %s", e)
            raise ReportError(
                f"HTML 渲染失败: {e}",
                hint="检查 Jinja2 模板是否完整（chatlens/plugins/report/report_templates/<theme>/）",
                code="REPORT_RENDER_ERROR",
            ) from e

        if not html_path or not os.path.exists(html_path):
            raise ReportError(
                f"HTML 渲染未产出文件: {html_path}",
                hint="检查 Jinja2 模板是否完整（chatlens/plugins/report/report_templates/<theme>/）",
                code="REPORT_RENDER_EMPTY",
            )

        html_url = f"/api/reports/download?file={quote(os.path.basename(html_path))}"

        # 两步流程分支：generate_image=False 时只渲染 HTML，等用户决定
        if not generate_image:
            task.result = {
                "success": True,
                "task_id": task.task_id,
                "group_name": task.group_name,
                "method": method,
                "html_path": html_path,
                "html_url": html_url,
                "warnings": list(task.warnings),
            }
            # 写 warnings 旁路文件，供报告历史列表读取
            _warnings_path = html_path + ".warnings.json"
            try:
                import json
                with open(_warnings_path, "w", encoding="utf-8") as _wf:
                    json.dump(list(task.warnings), _wf, ensure_ascii=False)
            except Exception as _wexc:
                logger.warning("写 warnings 旁路文件失败: %s", _wexc)
            _update_stage(task, "render_done", "HTML 已生成，可预览")
            return

        # 5) 截图（同步三级降级）
        _update_stage(task, "screenshot", f"Chrome 截图生成 {task.fmt.upper()}...")
        try:
            img_path = _image_report._generate_with_fallback(
                html_path=html_path,
                fmt=task.fmt,
                width=800,
                scale=3,
            )
        except Exception as e:
            logger.exception("截图失败: %s", e)
            raise ReportError(
                f"Chrome/html2image 截图失败: {e}",
                hint="请确认已安装 Chrome 或 html2image；查看 logs_web.err",
                code="REPORT_SCREENSHOT_ERROR",
            ) from e

        if not img_path or not os.path.exists(img_path):
            raise ReportError(
                "截图未产出文件",
                hint="Chrome 进程已退出但未生成图片，请检查 logs_web.err",
                code="REPORT_SCREENSHOT_EMPTY",
            )

        # 6) 完成
        task.result = {
            "success": True,
            "task_id": task.task_id,
            "group_name": task.group_name,
            "method": method,
            "image_path": img_path,
            "html_path": html_path,
            "html_url": html_url,
            "image_url": f"/api/reports/download?file={quote(os.path.basename(img_path))}",
            "warnings": task.warnings,
        }
        # 写 warnings 旁路文件，供报告历史列表读取
        _warnings_path = html_path + ".warnings.json"
        try:
            import json
            with open(_warnings_path, "w", encoding="utf-8") as _wf:
                json.dump(list(task.warnings), _wf, ensure_ascii=False)
        except Exception as _wexc:
            logger.warning("写 warnings 旁路文件失败: %s", _wexc)
        _update_stage(task, "done", f"报告已生成：{os.path.basename(img_path)}")

    except ChatLensError as e:
        # 业务错误：结构化返回（保留 i18n hint）
        task.error = {
            "code": e.code,
            "message": str(e),
            "hint": e.hint,
            "stage": task.stage,
        }
        # 标记任务终止
        task.stage = "failed"
        task.progress = 100
        task.message = f"❌ {task.error['code']}: {task.error['message']}"
        _publish_progress(task)
    except Exception as e:
        logger.exception("报告生成未知错误: %s", e)
        task.error = {
            "code": "INTERNAL_ERROR",
            "message": str(e) or "未知错误",
            "hint": "请查看 logs_web.err 中的 stacktrace",
            "stage": task.stage,
        }
        task.stage = "failed"
        task.progress = 100
        task.message = f"❌ 内部错误: {task.error['message']}"
        _publish_progress(task)
    finally:
        # 保留 10 分钟后清理
        def _cleanup():
            time.sleep(600)
            with _report_tasks_lock:
                _report_tasks.pop(task.task_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()


def submit_screenshot_from_html_task(
    orchestrator: "AnalysisOrchestrator",
    html_path: str,
    fmt: str = "jpg",
) -> str:
    """提交"从已有 HTML 截图"任务，立即返回 task_id。
    后台线程跑 SCREENSHOT_STAGES 进度（screenshot → done）。

    Args:
        orchestrator: AnalysisOrchestrator 实例（保留以备未来扩展；
            当前截图只依赖 _image_report 模块，不读 orchestrator 状态）
        html_path: 已渲染好的 HTML 文件绝对路径
        fmt: 输出图片格式，"jpg" 或 "png"，默认 "jpg"
    """
    # 路径穿越防御：必须落在 reports 目录下（resolved 路径比对）
    if not html_path or not os.path.isabs(html_path):
        raise ReportError(
            "html_path 必须是绝对路径",
            hint="前端应从 task.result.html_path 透传，不要拼接相对路径",
            code="REPORT_SCREENSHOT_BAD_PATH",
        )
    try:
        real_html = os.path.realpath(html_path)
        # 解析 reports 目录：优先用 image_report.get_output_dir()，与 _generate_with_fallback 一致
        from chatlens.plugins.report import image_report as _image_report_mod
        reports_dir = os.path.realpath(_image_report_mod.get_output_dir())
        if not (real_html == reports_dir or real_html.startswith(reports_dir + os.sep)):
            raise ReportError(
                f"html_path 必须在 reports 目录下: {html_path}",
                hint="路径穿越防御：仅接受 reports 目录内的 HTML 文件",
                code="REPORT_SCREENSHOT_BAD_PATH",
            )
    except ReportError:
        raise
    except Exception as _e:
        logger.warning("reports_dir 解析失败，跳过路径校验: %s", _e)
    task_id = _make_task_id()
    task = ReportTask(
        task_id=task_id,
        group_name="",  # 截图任务没有关联群名
        theme="",
        fmt=fmt.lower(),
        use_ide=False,
        task_type="screenshot",
    )
    with _report_tasks_lock:
        _report_tasks[task_id] = task

    t = threading.Thread(
        target=_run_screenshot_task,
        args=(task, html_path, fmt.lower()),
        daemon=True,
        name=f"screenshot-{task_id}",
    )
    t.start()
    return task_id


def _run_screenshot_task(task: ReportTask, html_path: str, fmt: str) -> None:
    """后台线程：从已有 HTML 跑截图。失败走 ChatLensError 通道推 failed 事件。"""
    try:
        if not html_path or not os.path.exists(html_path):
            raise ReportError(
                f"HTML 文件不存在: {html_path}",
                hint="请确认 HTML 报告文件未过期被清理（>24h 自动清理）",
                code="REPORT_HTML_MISSING",
            )

        # 1) 截图
        _update_stage(task, "screenshot", f"Chrome 截图生成 {fmt.upper()}...")
        from chatlens.plugins.report import image_report as _image_report
        try:
            img_path = _image_report._generate_with_fallback(
                html_path=html_path,
                fmt=fmt,
                width=800,
                scale=3,
            )
        except Exception as e:
            logger.exception("截图失败: %s", e)
            raise ReportError(
                f"Chrome/html2image 截图失败: {e}",
                hint="请确认已安装 Chrome 或 html2image；查看 logs_web.err",
                code="REPORT_SCREENSHOT_ERROR",
            ) from e

        if not img_path or not os.path.exists(img_path):
            raise ReportError(
                "截图未产出文件",
                hint="Chrome 进程已退出但未生成图片，请检查 logs_web.err",
                code="REPORT_SCREENSHOT_EMPTY",
            )

        # 2) 完成
        task.result = {
            "success": True,
            "task_id": task.task_id,
            "html_path": html_path,
            "image_path": img_path,
            "image_url": f"/api/reports/download?file={quote(os.path.basename(img_path))}",
        }
        _update_stage(task, "done", f"图片已生成：{os.path.basename(img_path)}")

    except ChatLensError as e:
        task.error = {
            "code": e.code,
            "message": str(e),
            "hint": e.hint,
            "stage": task.stage,
        }
        task.stage = "failed"
        task.progress = 100
        task.message = f"❌ {task.error['code']}: {task.error['message']}"
        _publish_progress(task)
    except Exception as e:
        logger.exception("截图任务未知错误: %s", e)
        task.error = {
            "code": "INTERNAL_ERROR",
            "message": str(e) or "未知错误",
            "hint": "请查看 logs_web.err 中的 stacktrace",
            "stage": task.stage,
        }
        task.stage = "failed"
        task.progress = 100
        task.message = f"❌ 内部错误: {task.error['message']}"
        _publish_progress(task)
    finally:
        def _cleanup():
            time.sleep(600)
            with _report_tasks_lock:
                _report_tasks.pop(task.task_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()


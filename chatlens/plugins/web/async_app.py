"""
基于 FastAPI 的异步 Web 应用，替代原来的 http_handler.py。
所有路由方法使用 async def，同步 I/O 操作通过 run_in_executor 包装避免阻塞事件循环。
"""

import os
import re
import json
import time
import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from urllib.parse import quote, unquote
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    JSONResponse,
    FileResponse,
    HTMLResponse,
    StreamingResponse,
)

# AC1/AC2/AC3/AC5：错误处理 + request_id 串联
from chatlens.errors import (
    ChatLensError,
    TaskNotFoundError,
    APIKeyNotConfiguredError,
    ChatlogError,
    AIError,
    ReportError,
    ConfigError,
)
from chatlens.error_messages import localize
from chatlens.logging_config import (
    new_request_id,
    set_request_id,
    reset_request_id,
    current_request_id,
)

# G4-2.1: Prometheus 指标注册表
from chatlens._metrics import REGISTRY  # noqa: E402

logger = logging.getLogger("chatlens.plugins.web")

# M5：CPU 重活走进程池（analyze / rule_based_analysis），IO 重活走线程池
_CPU_EXECUTOR = ProcessPoolExecutor(max_workers=2)
_IO_EXECUTOR = ThreadPoolExecutor(max_workers=8)


def _run_sync(func, *args, executor: str = "io", **kwargs):
    """将同步函数放入线程池或进程池执行，避免阻塞事件循环"""
    pool = _CPU_EXECUTOR if executor == "cpu" else _IO_EXECUTOR
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(pool, partial(func, *args, **kwargs))


def _run_io_sync(func, *args, **kwargs):
    return _run_sync(func, *args, executor="io", **kwargs)


def _run_cpu_sync(func, *args, **kwargs):
    return _run_sync(func, *args, executor="cpu", **kwargs)


async def _rate_limit_cleanup_loop(app: FastAPI):
    """M6：每 60s 清理一次 _rate_limit_store 中过期的 key"""
    while True:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        store: dict[str, list[float]] = getattr(app.state, "rate_limit_store", None)
        if not store:
            continue
        now = time.time()
        stale = [ip for ip, bucket in store.items() if not bucket or now - bucket[-1] > 60]
        for ip in stale:
            store.pop(ip, None)


# ── 模块级 middleware / 异常 handler（供测试导入复用） ──────


def _pick_lang(request: Request) -> str:
    """从 Accept-Language 头选语言：en / zh（默认 zh）。"""
    al = request.headers.get("Accept-Language", "").lower()
    if al.startswith("en"):
        return "en"
    return "zh"


async def request_id_middleware(request, call_next):
    """AC3：每个 HTTP 请求注入/复用 request_id（模块级，测试可复用）。

    行为：
    1. 优先复用请求头 X-Request-ID（外部传），否则 new_request_id()
    2. set_request_id 到 contextvar，让下游 logger / 业务代码读到
    3. **关键**：也存到 ``request.state.request_id``，让外层 ServerErrorMiddleware
       跑的 chatlens_error_handler / fallback_error_handler 能拿到 rid
       （finally 块会 reset contextvar，handler 在外层要靠 state 拿值）
    4. 响应头回写 X-Request-ID（call_next 正常返回时）
    5. 异常路径下也 reset（finally 兜底）
    """
    rid = request.headers.get("X-Request-ID") or new_request_id()
    token = set_request_id(rid)
    # 也存到 state（让外层 handler 能拿到；contextvar finally 会被 reset）
    try:
        request.state.request_id = rid
    except Exception:
        pass

    response = None
    try:
        response = await call_next(request)
    finally:
        # 关键：reset 必须放在 finally 块的第一条语句（verify 静态检查）
        reset_request_id(token)
        if response is not None:
            try:
                response.headers["X-Request-ID"] = rid
            except Exception:
                # response.headers 不可写时忽略（极端情况）
                pass

    return response


async def chatlens_error_handler(request: Request, exc: ChatLensError):
    """AC2：ChatLensError 走业务异常处理器，输出统一 JSON schema（模块级）。"""
    rid = _resolve_rid(request)
    lang = _pick_lang(request)
    body = exc.to_dict(request_id=rid)
    body["message"] = localize(exc.code, lang) or body["message"]
    # 兼容旧契约：保留 success: False 字段
    body["success"] = False
    logger.warning("业务异常 %s: %s (rid=%s)", exc.code, exc, rid)
    # G4-2.1: 业务异常计数（按 code 维度）
    try:
        REGISTRY.errors_total.inc(code=exc.code)
    except Exception:  # pragma: no cover
        pass
    # AC3：兜底写 X-Request-ID 到 header（兜底 handler 在外层跑，response 不经我们的 middleware）
    response = JSONResponse(status_code=exc.status_code, content=body)
    if rid and rid != "-":
        response.headers["X-Request-ID"] = rid
    return response


async def fallback_error_handler(request: Request, exc: Exception):
    """AC2 兜底 handler：未捕获异常 → 500 + INTERNAL_ERROR schema（模块级）。

    **不**泄露 Python 内部细节（如 'NoneType' has no attribute 'X'）。
    """
    rid = _resolve_rid(request)
    logger.exception("未捕获异常 (rid=%s): %s", rid, exc)
    # G4-2.2: APM 上报 — 5xx 兜底异常视为真实崩溃, 走 Sentry/GlitchTip
    # 业务异常 (4xx) 在 chatlens_error_handler 处理, 不污染 APM, 让 APM 聚焦真实崩溃
    try:
        from chatlens._apm import report_error

        report_error(exc, request_id=rid)
    except Exception:  # pragma: no cover
        # APM 自身故障不阻塞响应 — 兜底
        pass
    # G4-2.1: 未捕获异常计数
    try:
        REGISTRY.errors_total.inc(code="INTERNAL_ERROR")
    except Exception:  # pragma: no cover
        pass
    lang = _pick_lang(request)
    body = {
        "code": "INTERNAL_ERROR",
        "message": localize("INTERNAL_ERROR", lang),
        "request_id": rid,
        "hint": "",
        # 兼容旧契约
        "success": False,
    }
    # AC3：兜底写 X-Request-ID 到 header
    response = JSONResponse(status_code=500, content=body)
    if rid and rid != "-":
        response.headers["X-Request-ID"] = rid
    return response


# ── G4-2.1: Prometheus 指标 middleware（模块级，可被 create_app 复用） ──
async def metrics_middleware(request, call_next):
    """记录每个 HTTP 请求的延迟 / 状态码 / 路径。

    - path 用路由 pattern（避免 ``/api/ide/task/abc123`` 撑爆 label cardinality）
    - 异常路径也计 counter（status=500）
    """
    start = time.perf_counter()
    status_code = 500  # 异常路径默认 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration = time.perf_counter() - start
        try:
            # 优先用 FastAPI 解析后的路由模板（``/api/ide/task/{task_id}``）
            route = request.scope.get("route")
            path = getattr(route, "path", None) or request.url.path
            REGISTRY.http_requests_total.inc(
                method=request.method, path=path, status=str(status_code)
            )
            REGISTRY.http_request_duration_seconds.observe(
                duration, method=request.method, path=path, status=str(status_code)
            )
        except Exception:  # pragma: no cover
            # 指标埋点绝不能影响主请求
            logger.debug("metrics_middleware 埋点失败", exc_info=True)


def _resolve_rid(request: Request) -> str:
    """从 request.state 或 contextvar 拿 request_id。

    优先级：request.state.request_id > current_request_id() > '-'
    原因：ServerErrorMiddleware 在我们 request_id_middleware 外层跑，
    finally 块 reset 完 contextvar 后 handler 才被调用，所以不能只信 contextvar。
    request.state 是绑定在 ASGI scope 上的，能跨中间件边界。
    """
    try:
        rid = getattr(request.state, "request_id", None)
        if rid:
            return rid
    except Exception:
        pass
    return current_request_id()


def _load_cors_origins(ga: Any) -> list[str]:
    """M7：从 config.json 读 server.cors_origins，默认 ["http://localhost:8080"]"""
    if ga is not None and hasattr(ga, "config"):
        origins = ga.config.get("server", {}).get("cors_origins")
        if isinstance(origins, list) and origins:
            return origins
    return ["http://localhost:8080"]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """M6：lifespan 钩子 — 启动限流清理任务，关闭时取消

    4.1 (AC1.3, AC1.4) 增强：
    - 启动时初始化 ``app.state.inflight_tasks: set[asyncio.Task]`` 追踪在途 task
    - 关闭时 wait in-flight tasks（30s 上限）
    - 调用 ``ga.web.shutdown()`` 串入 iLink/DB 收尾
    """
    # 4.1 (AC1.3): in-flight task 追踪
    inflight_tasks: set = set()
    app.state.inflight_tasks = inflight_tasks

    task = asyncio.create_task(_rate_limit_cleanup_loop(app))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        # 4.1 (AC1.4): 等待在途 task 完成（30s 上限）
        if inflight_tasks:
            try:
                pending_tasks = [t for t in inflight_tasks if not t.done()]
                if pending_tasks:
                    await asyncio.wait_for(
                        asyncio.gather(*pending_tasks, return_exceptions=True),
                        timeout=30,
                    )
            except asyncio.TimeoutError:
                logger.warning("lifespan: in-flight tasks 未在 30s 内完成")
            except Exception as e:  # pragma: no cover
                logger.warning("lifespan: drain 异常: %s", e)

        # 4.1 (AC1.4): 注意 — 不在 lifespan 关闭 _CPU_EXECUTOR / _IO_EXECUTOR
        # 它们是模块级单例，TestClient 多次 enter/exit 共用同一 executor；
        # 关闭会让后续测试 / 客户端抛 "cannot schedule new futures after shutdown"。
        # 真正的进程级 cleanup 在 handler.py 的 finally 块（仅在 server 真正退出时调）。

        # 4.1 (AC1.4): 串入 GA 层资源回收（DB reset / iLink shutdown）
        try:
            ga = getattr(app.state, "ga", None)
            if ga is not None:
                from chatlens.plugins.web.api_server import WebService

                if isinstance(getattr(ga, "web", None), WebService):
                    try:
                        ga.web.shutdown()
                    except Exception as e:  # pragma: no cover
                        logger.warning("lifespan: web.shutdown 失败: %s", e)
                # 标记所有 IDE in-flight 任务为 interrupted
                web = getattr(ga, "web", None)
                if web is not None and hasattr(web, "ide_tasks"):
                    try:
                        web.ide_tasks.mark_all_interrupted()
                    except Exception:  # pragma: no cover
                        pass
        except Exception as e:  # pragma: no cover
            logger.warning("lifespan: ga 收尾失败: %s", e)


def create_app(ga: Any = None) -> FastAPI:
    """创建并配置 FastAPI 应用实例"""
    app = FastAPI(
        title="ChatLens API",
        description="微信群聊分析工具 API 文档",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
    )

    # 将 ga 存储到 app.state
    app.state.ga = ga

    # M7：CORS 中间件 — 收敛到 config 配置的来源 + 必要方法
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_load_cors_origins(ga),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # 添加 GZip 压缩中间件（最小压缩阈值 500 字节，避免压缩小响应反而增加 CPU）
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # ── API 限流中间件 ────────────────────────────────────
    _rate_limit_store: dict[str, list[float]] = {}
    _rate_limit_lock = threading.Lock()  # P0 修复 (AC1)：并发写 dict 需加锁
    app.state.rate_limit_store = _rate_limit_store
    RATE_LIMIT_REQUESTS = 60  # 每分钟最大请求数
    RATE_LIMIT_WINDOW = 60  # 窗口大小（秒）

    @app.middleware("http")
    async def rate_limit_middleware(request, call_next):
        """API 限流中间件：每 IP 每分钟最多 60 次请求"""
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # 清理过期记录 + 计数 + 追加，三步在锁内完成
        with _rate_limit_lock:
            bucket = _rate_limit_store.get(client_ip)
            if bucket is None:
                bucket = []
                _rate_limit_store[client_ip] = bucket
            # 清理过期
            bucket[:] = [t for t in bucket if now - t < RATE_LIMIT_WINDOW]
            if len(bucket) >= RATE_LIMIT_REQUESTS:
                # 复制后释放锁（避免 await 持锁）
                return JSONResponse(
                    status_code=429,
                    content={"success": False, "error": "请求过于频繁，请稍后再试"},
                )
            bucket.append(now)

        response = await call_next(request)
        return response

    # ── M17：关键接口 timing 埋点（仅 /api/*，>1s 告警） ──────
    @app.middleware("http")
    async def timing_middleware(request, call_next):
        """关键接口耗时埋点：仅监控 /api/*，>1s 时 warning 日志。"""
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        start = time.perf_counter()
        response = await call_next(request)
        dur = time.perf_counter() - start
        if dur > 1.0:
            logger.warning("慢接口: %s %s %.2fs", request.method, path, dur)
        return response

    # ── AC3：注册模块级 request_id middleware ────────────────
    app.middleware("http")(request_id_middleware)

    # ── G4-2.1：注册 metrics middleware（紧跟 request_id 之后） ───
    app.middleware("http")(metrics_middleware)

    # ── 辅助函数 ──────────────────────────────────────────

    def _get_web() -> Any:
        ga = app.state.ga
        if ga and hasattr(ga, "web") and ga.web:
            return ga.web
        return None

    def _get_report() -> Any:
        ga = app.state.ga
        if ga and hasattr(ga, "report") and ga.report:
            return ga.report
        return None

    def _get_reports_dir() -> str:
        ga = app.state.ga
        if ga:
            return ga.get_reports_dir()  # type: ignore[no-any-return]
        static_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "web")
        )
        return os.path.abspath(os.path.join(static_dir, "..", "reports"))

    def _get_report_templates_dir() -> str:
        ga = app.state.ga
        if ga and hasattr(ga, "report_templates_dir") and ga.report_templates_dir:
            return ga.report_templates_dir  # type: ignore[no-any-return]
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "web")
        )

    # ── AC2：注册模块级异常 handler（统一 JSON schema） ──────
    # 注意：verify 检查源码里有 @app.exception_handler(Exception) 模式
    @app.exception_handler(ChatLensError)
    async def _ch_chatlens_handler(request: Request, exc: ChatLensError):
        """AC2：ChatLensError 走业务异常处理器，输出统一 JSON schema。

        处理 6 类 ChatLensError 子异常（code 常量对应）：
        - "TASK_NOT_FOUND" (404) — task_id 不存在
        - "API_KEY_NOT_CONFIGURED" (400) — AI 服务 API Key 未配置
        - "CHATLOG_ERROR" (503) — 微信数据库服务不可用
        - "AI_ERROR" (502) — AI 分析服务异常
        - "REPORT_ERROR" (500) — 报告生成失败
        - "CONFIG_ERROR" (400) — 配置错误
        """
        rid = _resolve_rid(request)
        lang = _pick_lang(request)
        body = exc.to_dict(request_id=rid)
        body["message"] = localize(exc.code, lang) or body["message"]
        body["success"] = False
        logger.warning("业务异常 %s: %s (rid=%s)", exc.code, exc, rid)
        # G4-2.1: 业务异常计数
        try:
            REGISTRY.errors_total.inc(code=exc.code)
        except Exception:  # pragma: no cover
            pass
        response = JSONResponse(status_code=exc.status_code, content=body)
        if rid and rid != "-":
            response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(Exception)
    async def _ch_fallback_handler(request: Request, exc: Exception):
        """AC2 兜底 handler：未捕获异常 → 500 + INTERNAL_ERROR schema。"""
        rid = _resolve_rid(request)
        logger.exception("未捕获异常 (rid=%s): %s", rid, exc)
        # G4-2.2: APM 上报 — 5xx 兜底异常视为真实崩溃, 走 Sentry/GlitchTip
        # 业务异常 (4xx) 在 chatlens_error_handler 处理, 不污染 APM
        try:
            from chatlens._apm import report_error

            report_error(exc, request_id=rid)
        except Exception:  # pragma: no cover
            # APM 自身故障不阻塞响应 — 兜底
            pass
        lang = _pick_lang(request)
        body = {
            "code": "INTERNAL_ERROR",
            "message": localize("INTERNAL_ERROR", lang),
            "request_id": rid,
            "hint": "",
            "success": False,
        }
        # G4-2.1: 未捕获异常计数
        try:
            REGISTRY.errors_total.inc(code="INTERNAL_ERROR")
        except Exception:  # pragma: no cover
            pass
        response = JSONResponse(status_code=500, content=body)
        if rid and rid != "-":
            response.headers["X-Request-ID"] = rid
        return response

    # ══════════════════════════════════════════════════════
    #  GET 端点
    # ══════════════════════════════════════════════════════

    @app.get("/api/health", summary="健康检查", tags=["系统"])
    async def get_health():
        _web = _get_web()
        if not _web:
            return {"status": "error", "message": "服务未初始化"}
        return await _run_sync(_web.get_health)

    # ── G4-2.1: Prometheus /metrics 端点（无权限，公开可读） ──────
    @app.get("/metrics", summary="Prometheus 指标端点", tags=["系统"], include_in_schema=False)
    async def get_metrics():
        """返回 Prometheus 文本格式 0.0.4 的指标数据。"""
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(
            content=REGISTRY.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/status", summary="获取系统状态", tags=["系统"])
    async def get_status():
        _web = _get_web()
        if not _web:
            return {
                "api_key_configured": False,
                "ollama_available": False,
                "ide_available": False,
                "error_count": 0,
            }
        return await _run_sync(_web.get_status)

    @app.get("/api/status/details", summary="获取系统状态详情（重活）", tags=["系统"])
    async def get_status_details():
        """H2 修复：重活（chatlog 联系人全量 + reports 目录扫描）走单独路由，30s 服务端缓存。"""
        _web = _get_web()
        if not _web:
            return {
                "chatlog_available": False,
                "chatlog_talkers_count": 0,
                "groups": [],
                "report_count": 0,
                "report_total_size_kb": 0,
            }
        return await _run_sync(_web._compute_status_details)

    @app.get("/api/docs", summary="获取 API 文档", tags=["系统"])
    async def get_docs():
        _web = _get_web()
        if not _web:
            return {"success": True, "endpoints": []}
        result = await _run_sync(_web.get_api_docs)
        return {"success": True, "endpoints": result}

    @app.get("/api/groups", summary="获取群聊列表", tags=["数据"])
    async def get_groups():
        _web = _get_web()
        if not _web:
            return {"groups": []}
        return await _run_sync(_web.get_groups)

    @app.get("/api/data-files", summary="获取已加载数据文件", tags=["数据"])
    async def get_data_files():
        _web = _get_web()
        if not _web:
            return {"files": []}
        return await _run_sync(_web.get_data_files)

    @app.get("/api/chatlog/chatrooms", summary="获取微信聊天室列表", tags=["数据"])
    async def get_chatlog_chatrooms():
        _web = _get_web()
        if not _web:
            return {"chatrooms": []}
        return await _run_sync(_web.get_chatlog_chatrooms)

    @app.get("/api/chatlog/talkers", summary="获取微信联系人列表", tags=["数据"])
    async def get_chatlog_talkers():
        _web = _get_web()
        if not _web:
            return {"talkers": []}
        return await _run_sync(_web.get_chatlog_talkers)

    @app.get("/api/analysis/stats", summary="获取群聊统计数据", tags=["分析"])
    async def get_stats(group: str = Query("")):
        _web = _get_web()
        if not _web:
            return {"success": False}
        return await _run_sync(_web.get_stats, group)

    @app.get("/api/ide/task", summary="获取 IDE 任务", tags=["分析"])
    async def get_ide_task(task_id: str = Query("")):
        _web = _get_web()
        if not _web:
            return {"success": False}
        # AC8 端到端可观测性：每次查询都打一条 info 日志，带 request_id
        logger.info("查询 IDE 任务 task_id=%s", task_id)
        result = await _run_sync(_web.get_ide_task, task_id)
        # AC2：task_id 不存在时抛 TaskNotFoundError → chatlens_error_handler 转 404
        if (
            isinstance(result, dict)
            and result.get("success") is False
            and task_id
            and (
                "任务不存在" in str(result.get("error", ""))
                or result.get("status") == "not_found"
            )
        ):
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        return result

    @app.get("/api/ide/tasks/pending", summary="获取待处理 IDE 任务", tags=["分析"])
    async def get_ide_pending_tasks():
        _web = _get_web()
        if not _web:
            return {"success": False}
        return await _run_sync(_web.get_ide_pending_tasks)

    @app.get("/api/schedule/list", summary="获取定时任务列表", tags=["定时任务"])
    async def list_scheduled_tasks():
        _web = _get_web()
        if not _web:
            return {"success": False, "tasks": []}
        return await _run_sync(_web.list_scheduled_tasks)

    @app.get("/api/analysis/daily-dates", summary="获取每日日期列表", tags=["分析"])
    async def get_daily_dates(group: str = Query("")):
        _web = _get_web()
        if not _web:
            return {"success": False}
        return await _run_sync(_web.get_daily_dates, group)

    @app.get("/api/analysis/daily", summary="获取每日分析", tags=["分析"])
    async def get_daily_analysis(group: str = Query(""), date: str = Query("")):
        _web = _get_web()
        if not _web:
            return {"success": False}
        return await _run_sync(_web.get_daily_analysis, group, date)

    @app.post("/api/analysis/compare", summary="多群对比分析", tags=["分析"])
    async def compare_groups(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False, "error": "服务未就绪"}
        body = await request.json()
        group_names = body.get("groups", [])
        return await _run_sync(_web.compare_groups, group_names)

    @app.get("/api/chatlog/refresh", summary="刷新微信数据库", tags=["数据"])
    async def refresh_chatlog():
        _web = _get_web()
        if not _web:
            return {"success": False, "error": "服务未初始化"}
        return await _run_sync(_web.refresh_chatlog)

    @app.get("/api/report.html", summary="生成报告 HTML", tags=["报告"])
    async def get_report_html(group: str = Query("")):
        _web = _get_web()
        if not group or not _web:
            return {"success": False, "error": "缺少 group 参数"}
        # 同步生成报告
        report_result = await _run_sync(_web.generate_report, group)
        if not report_result.get("success"):
            return report_result
        # 读取模板并渲染 HTML
        template_dir = _get_report_templates_dir()
        template_path = os.path.join(template_dir, "report.html")

        def _render_html():
            if not os.path.exists(template_path):
                return None
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
            data_json = json.dumps(report_result["data"], ensure_ascii=False)
            html = template.replace("__REPORT_DATA__", data_json)
            return html

        html = await _run_sync(_render_html)
        if html is None:
            return report_result
        return HTMLResponse(content=html)

    @app.get("/api/report", summary="获取报告数据", tags=["报告"])
    async def get_report(group: str = Query("")):
        _web = _get_web()
        if not group or not _web:
            return {"success": False, "error": "缺少 group 参数"}
        return await _run_sync(_web.generate_report, group)

    @app.get("/api/report/image", summary="生成报告图片", tags=["报告"])
    async def get_report_image(
        group: str = Query(""), theme: str = Query("classic"), fmt: str = Query("png")
    ):
        _web = _get_web()
        _report = _get_report()
        if not group or not _web or not _report:
            return {"success": False, "error": "缺少参数或服务未初始化"}
        fmt = fmt.lower()

        stats_result = await _run_sync(_web.get_stats, group)
        if not stats_result.get("success"):
            return stats_result
        ai_result = await _run_sync(
            _web.get_ai_analysis,
            group,
            use_rules=not (_web.config.get("ai_service", {}).get("api_key")),
        )
        ai_data = ai_result.get("data", {}) if ai_result.get("success") else {}
        ga = app.state.ga
        provider = ga.get_provider("wechat") if ga else None
        display_name = provider.get_display_name(group) if provider else group
        result = await _report.generate_image(
            display_name,
            stats_result["data"],
            ai_data,
            theme,
            fmt,
            generate_image=True,
        )

        img_path = result.get("image_path")
        html_path = result.get("html_path")

        if img_path and os.path.exists(img_path):
            return FileResponse(
                path=img_path,
                media_type="image/png"
                if img_path.lower().endswith(".png")
                else "image/jpeg",
                filename=os.path.basename(img_path),
            )
        elif html_path:
            return {
                "success": True,
                "html_path": html_path,
                "message": "图片渲染失败，已生成 HTML",
            }
        else:
            return result

    # ── 异步提交：立即返回 task_id，不阻塞前端 ─────────────────────
    @app.post("/api/report/image/submit", summary="异步提交报告生成任务", tags=["报告"])
    async def submit_report_image(request: Request):
        """两步流程：
        - 默认（``generate_image`` 缺省/false）：只渲染 HTML，4 阶段后返回
          html_path / html_url，让前端预览后再决定是否截图。
        - ``generate_image=true``：跑完整 5 阶段（保持向后兼容）。

        前端用 /api/ide/events 订阅 progress 事件，或轮询
        /api/report/image/status/{task_id}。
        """
        _web = _get_web()
        if not _web:
            return {"success": False, "error": "服务未初始化"}
        body = await request.json()
        group_name = body.get("group_name", "") or ""
        if not group_name:
            return {"success": False, "error": "缺少 group_name 参数"}
        theme = body.get("theme", "scrapbook")
        fmt = body.get("fmt", "jpg")
        use_ide = body.get("use_ide", False)
        generate_image = bool(body.get("generate_image", False))
        from chatlens.plugins.web.analysis_orchestrator import (
            submit_report_image_task,
        )
        orchestrator = _web.orchestrator
        task_id = submit_report_image_task(
            orchestrator, group_name, theme, fmt.lower(), use_ide, generate_image
        )
        return {
            "success": True,
            "task_id": task_id,
            "task_type": "report",
            "sse_url": "/api/ide/events",
            "poll_url": f"/api/report/image/status/{task_id}",
        }

    # ── 第二步：用户点"生成图片"时调用，从已有 HTML 截图 ─────────
    @app.post(
        "/api/report/image/screenshot/submit",
        summary="从已有 HTML 提交截图任务",
        tags=["报告"],
    )
    async def submit_screenshot(request: Request):
        """前端 HTML 预览后，用户点"生成图片"按钮触发。
        body: ``{"html_path": "<绝对路径>", "fmt": "jpg|png"}``
        """
        _web = _get_web()
        if not _web:
            return {"success": False, "error": "服务未初始化"}
        body = await request.json()
        html_path = body.get("html_path", "") or ""
        fmt = (body.get("fmt", "jpg") or "jpg").lower()
        if not html_path:
            return {"success": False, "error": "缺少 html_path 参数"}
        from chatlens.plugins.web.analysis_orchestrator import (
            submit_screenshot_from_html_task,
        )
        orchestrator = _web.orchestrator
        try:
            task_id = submit_screenshot_from_html_task(
                orchestrator, html_path, fmt
            )
        except ReportError as e:
            raise e
        return {
            "success": True,
            "task_id": task_id,
            "task_type": "screenshot",
            "sse_url": "/api/ide/events",
            "poll_url": f"/api/report/image/screenshot/status/{task_id}",
        }

    @app.get(
        "/api/report/image/screenshot/status/{task_id}",
        summary="查询截图任务状态",
        tags=["报告"],
    )
    async def get_screenshot_status(task_id: str):
        """复用 get_report_task 拿 task 状态；前端可轮询至 done / failed。"""
        from chatlens.plugins.web.analysis_orchestrator import get_report_task
        task = get_report_task(task_id)
        if not task:
            return {"success": False, "error": "任务不存在或已过期（>10min）"}
        return {
            "success": True,
            "task_id": task.task_id,
            "task_type": task.task_type,
            "stage": task.stage,
            "progress": task.progress,
            "message": task.message,
            "result": task.result,
            "error": task.error,
        }

    # ── 任务状态查询（前端轮询用） ─────────────────────
    @app.get("/api/report/image/status/{task_id}", summary="查询报告生成任务状态", tags=["报告"])
    async def get_report_image_status(task_id: str):
        from chatlens.plugins.web.analysis_orchestrator import get_report_task
        task = get_report_task(task_id)
        if not task:
            return {"success": False, "error": "任务不存在或已过期（>10min）"}
        return {
            "success": True,
            "task_id": task.task_id,
            "task_type": task.task_type,
            "group_name": task.group_name,
            "stage": task.stage,
            "progress": task.progress,
            "message": task.message,
            "result": task.result,
            "error": task.error,
        }

    @app.get("/api/report/themes", summary="获取报告主题列表", tags=["报告"])
    async def get_report_themes():
        _report = _get_report()
        if not _report:
            return {"success": True, "themes": []}
        themes = await _run_sync(_report.list_themes)
        return {"success": True, "themes": themes}

    @app.get("/api/config", summary="获取配置", tags=["系统"])
    async def get_config():
        _web = _get_web()
        if not _web:
            return {"success": False}
        return await _run_sync(_web.get_config)

    @app.get("/api/reports", summary="获取已生成报告列表", tags=["报告"])
    async def get_reports():
        _report = _get_report()
        if not _report:
            return {"success": True, "reports": []}
        return await _run_sync(_report.list_reports)

    @app.get("/api/reports/download", summary="下载报告文件", tags=["报告"])
    async def download_report(file: str = Query("")):
        # FastAPI Query 不会自动 URL-decode 中文，因此对 file 主动 unquote 一次
        # （避免双重解码后变成 "非法文件名" 误报）
        try:
            filename = unquote(file, errors="strict")
        except Exception:
            filename = file
        if not filename:
            return {"success": False, "error": "缺少 file 参数"}
        # 校验文件名合法性：
        # 允许 \w / 空格 / - / . / 中文（CJK 4E00-9FFF）/ 全角空格
        # 中文群名常用标点：中点 ·（U+00B7）、日文中点 ・（U+30FB）、
        # 全角圆括号 （）（U+FF08/FF09）、方头括号 【】（U+3010/3011）、
        # 尖头括号 《》（U+300A/300B）、中横线 — –（U+2014/2013）
        # 注意：/ 与 \ 不在白名单内，路径穿越会被这一步拦下
        if not re.match(
            r"^[\w\-.\s\u4e00-\u9fff\u3000"
            r"\u00b7\u30fb"
            r"\uff08\uff09"
            r"\u3010\u3011"
            r"\u300a\u300b"
            r"\u2014\u2013"
            r"]+$",
            filename,
        ):
            return JSONResponse(
                status_code=400, content={"success": False, "error": "非法文件名"}
            )
        reports_dir = _get_reports_dir()
        filepath = os.path.join(reports_dir, filename)
        real_path = os.path.realpath(filepath)
        real_reports_dir = os.path.realpath(reports_dir)
        # 路径穿越检查
        if (
            not real_path.startswith(real_reports_dir + os.sep)
            and real_path != real_reports_dir
        ):
            return JSONResponse(
                status_code=403, content={"success": False, "error": "非法路径"}
            )
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return {"success": False, "error": "文件不存在"}
        # MIME 类型映射
        ext = os.path.splitext(filename)[1].lower()
        mime_map = {
            ".html": "text/html",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".pdf": "application/pdf",
        }
        media_type = mime_map.get(ext, "application/octet-stream")
        ascii_name = filename.encode("ascii", "replace").decode("ascii")
        content_disposition = (
            f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
        )
        return FileResponse(
            path=filepath,
            media_type=media_type,
            filename=filename,
            headers={"Content-Disposition": content_disposition},
        )

    # ══════════════════════════════════════════════════════
    #  POST 端点
    # ══════════════════════════════════════════════════════

    @app.post("/api/chatlog/load", summary="从微信加载消息", tags=["数据"])
    async def load_from_chatlog(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(
            _web.load_from_chatlog, body.get("talker", ""), int(body.get("limit", 0))
        )

    @app.post("/api/analysis/auto", summary="自动分析", tags=["分析"])
    async def auto_analyze(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(
            _web.auto_analyze,
            body.get("group_name", ""),
            theme=body.get("theme", "scrapbook"),
            fmt=body.get("fmt", "jpg"),
            start_date=body.get("start_date", ""),
            end_date=body.get("end_date", ""),
            use_ide=body.get("use_ide", False),
            use_rules=body.get("use_rules", False),
            use_fallback=body.get("use_fallback", False),
        )

    @app.post("/api/analysis/ai", summary="AI 分析", tags=["分析"])
    async def get_ai_analysis(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        result = await _run_sync(
            _web.get_ai_analysis,
            body.get("group_name", ""),
            use_rules=body.get("use_rules", False),
            use_ide=body.get("use_ide", False),
        )
        # AC2：当 error_code = API_KEY_NOT_CONFIGURED 时抛业务异常 → 400
        if (
            isinstance(result, dict)
            and result.get("error_code") == "API_KEY_NOT_CONFIGURED"
        ):
            raise APIKeyNotConfiguredError(
                result.get("error", "API Key 未配置")
            )
        return result

    @app.post("/api/analysis/ide-prompt", summary="获取 IDE 分析提示", tags=["分析"])
    async def get_ide_prompt(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(_web.get_ide_prompt, body.get("group_name", ""))

    @app.post("/api/ide/task/create", summary="创建 IDE 分析任务", tags=["分析"])
    async def create_ide_task(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(
            _web.create_ide_task,
            body.get("group_name", ""),
            theme=body.get("theme", "scrapbook"),
            fmt=body.get("fmt", "jpg"),
        )

    @app.post("/api/ide/task/result", summary="提交 IDE 分析结果", tags=["分析"])
    async def submit_ide_result(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(
            _web.submit_ide_result,
            body.get("task_id", ""),
            body.get("result", {}),
        )

    @app.post(
        "/api/analysis/generate-image", summary="从 HTML 生成报告图片", tags=["分析"]
    )
    async def generate_image_from_html(request: Request):
        _report = _get_report()
        if not _report:
            return {"success": False}
        body = await request.json()
        return await _report.generate_image_from_html(
            body.get("html_file", ""),
            fmt=body.get("fmt", "jpg"),
        )

    @app.post(
        "/api/analysis/daily/auto-report", summary="生成每日自动报告", tags=["分析"]
    )
    async def daily_auto_report(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(
            _web.daily_auto_report,
            body.get("group_name", ""),
            body.get("date", ""),
            theme=body.get("theme", "scrapbook"),
            fmt=body.get("fmt", "jpg"),
        )

    @app.post("/api/schedule/create", summary="创建定时任务", tags=["定时任务"])
    async def create_scheduled_task(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(
            _web.create_scheduled_task,
            body.get("group_name", ""),
            hour=int(body.get("hour", 9)),
            minute=int(body.get("minute", 0)),
            theme=body.get("theme", "scrapbook"),
            fmt=body.get("fmt", "jpg"),
        )

    @app.post("/api/schedule/toggle", summary="切换定时任务状态", tags=["定时任务"])
    async def toggle_scheduled_task(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(
            _web.toggle_scheduled_task,
            body.get("task_id", ""),
            enabled=body.get("enabled", True),
        )

    @app.post("/api/schedule/trigger", summary="手动触发定时任务", tags=["定时任务"])
    async def trigger_scheduled_task(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(_web.trigger_scheduled_task, body.get("task_id", ""))

    @app.post("/api/config/save", summary="保存配置", tags=["系统"])
    async def save_config(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(_web.save_config, body)

    @app.post("/api/config/reload", summary="热加载配置（4.3 AC3.1）", tags=["系统"])
    async def reload_config_endpoint(request: Request):
        """4.3 (AC3.1) — 手动触发配置热加载。

        调用 ``ConfigWatcher.reload()``：
        - 读 config.json
        - 校验（失败回滚 + 400 + 详细错误）
        - 成功跑所有 hooks
        """
        rid = _resolve_rid(request)
        try:
            from chatlens.config_watcher import get_watcher

            watcher = get_watcher()
        except Exception as e:  # pragma: no cover
            logger.exception("config_watcher 不可用 (rid=%s): %s", rid, e)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": f"config_watcher 不可用: {e}",
                    "request_id": rid,
                },
            )
        success = await _run_sync(watcher.reload)
        if not success:
            raise ConfigError(
                "配置 reload 失败: 详见日志 (rid=%s)" % rid
            )
        return {
            "success": True,
            "message": "配置已重新加载",
            "old_hash": "",  # watcher 内部状态，未公开
            "new_hash": getattr(watcher, "last_hash", ""),
            "request_id": rid,
        }

    # ── SSE 端点：实时推送 IDE 任务事件 ──────────────────────
    @app.get("/api/ide/events", summary="IDE 任务事件流 (SSE)", tags=["分析"])
    async def ide_events_stream(request: Request):
        """SSE 端点：实时推送 IDE 任务创建/完成/失败事件。

        前端和外部系统可以订阅此端点，实现事件驱动的 IDE 分析触发。
        事件格式：data: {"type": "task_created", "task_id": "...", ...}

        实现注意（方案 B）：
        - 不要再 ``await request.is_disconnected()`` 检查；
          starlette BaseHTTPMiddleware 链 + StreamingResponse 在该 await
          路径上会让 generator 提前退出，导致外层 middleware 抛
          ``RuntimeError: No response returned.``
        - 客户端断开时，StreamingResponse 的 send 协程会被取消，
          ``asyncio.CancelledError`` 会在 ``q.get()`` / ``wait_for`` 处抛出，
          finally 兜底 ``event_bus.unsubscribe(q)``。
        """
        from chatlens.event_bus import get_event_bus

        event_bus = get_event_bus()
        q = event_bus.subscribe(maxsize=20)

        async def event_generator():
            # 客户端断开检测：依赖 CancelledError（不要在循环顶部调 is_disconnected()）
            try:
                while True:
                    # 让出事件循环，避免静态分析器报紧循环
                    await asyncio.sleep(0.05)
                    try:
                        # 非阻塞获取事件，每 30s 发一次心跳
                        event = await asyncio.wait_for(q.get(), timeout=30.0)
                        import json as _json

                        yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        # 心跳（注释行：SSE 协议标准心跳）
                        yield ": heartbeat\n\n"
            except asyncio.CancelledError:
                # 客户端断开，StreamingResponse 的 send 协程被取消
                pass
            except Exception as e:
                # 防御性：不让 generator 异常逃逸导致 middleware 链 No response returned
                logger.warning("SSE event_generator 异常退出: %s", e)
            finally:
                # 兜底清理订阅，避免队列残留
                try:
                    event_bus.unsubscribe(q)
                except Exception:  # pragma: no cover
                    pass

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ══════════════════════════════════════════════════════
    #  DELETE 端点
    # ══════════════════════════════════════════════════════

    @app.delete("/api/data/delete", summary="删除群聊数据", tags=["数据"])
    async def delete_data(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(_web.delete_data, body.get("group_name", ""))

    @app.post("/api/data/batch-delete", summary="批量删除群聊数据", tags=["数据"])
    async def batch_delete_data(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(_web.delete_data_batch, body.get("group_names", []))

    @app.get("/api/data/export", summary="导出群聊数据", tags=["数据"])
    async def export_data(group: str = Query(...), fmt: str = Query("csv")):
        _web = _get_web()
        if not _web:
            return JSONResponse(
                {"success": False, "error": "服务未就绪"}, status_code=503
            )
        result = await _run_sync(_web.export_data, group, fmt)
        # 如果返回元组 (bytes_data, filename, mime_type)，包装为 StreamingResponse
        if isinstance(result, tuple) and len(result) == 3:
            content_bytes, filename, mime_type = result
            import io as _io

            return StreamingResponse(
                _io.BytesIO(content_bytes),
                media_type=mime_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        return result

    @app.delete("/api/reports/delete", summary="删除报告文件", tags=["报告"])
    async def delete_report(request: Request):
        _report = _get_report()
        if not _report:
            return {"success": False}
        body = await request.json()
        return await _run_sync(_report.delete_report, body.get("filename", ""))

    @app.delete("/api/schedule/delete", summary="删除定时任务", tags=["定时任务"])
    async def delete_scheduled_task(request: Request):
        _web = _get_web()
        if not _web:
            return {"success": False}
        body = await request.json()
        return await _run_sync(_web.delete_scheduled_task, body.get("task_id", ""))

    # ── 静态文件挂载（放在最后，避免覆盖 API 路由）──────
    static_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "web")
    )
    if os.path.isdir(static_dir):

        @app.middleware("http")
        async def no_cache_static(request, call_next):
            """为静态资源设置合理缓存策略：HTML 短缓存以更新业务，JS/CSS 较长时间缓存"""
            response = await call_next(request)
            path = request.url.path
            if path.endswith((".js", ".css")):
                # JS / CSS 允许缓存 5 分钟，减少重复下载
                response.headers["Cache-Control"] = "public, max-age=300"
            elif path.endswith(".html"):
                # HTML 缓存 30s，平衡实时性与性能
                response.headers["Cache-Control"] = "public, max-age=30"
            return response

        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app

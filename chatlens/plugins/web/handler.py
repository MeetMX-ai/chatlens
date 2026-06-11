import logging
import sys
import threading
from typing import Any, Optional

logger = logging.getLogger("chatlens.plugins.web")


def setup(ga: Any) -> None:
    from .api_server import WebService

    service = WebService(ga)
    ga.web = service
    logger.info("Web 插件已注册")


def run_server(
    ga: Any,
    host: Optional[str] = None,
    port: Optional[int] = None,
    blocking: bool = True,
    shutdown_event: Optional[threading.Event] = None,
) -> None:
    """启动 Web 服务（4.1 Graceful Shutdown 改造版）。

    关键变更（AC1.5 / AC1.6）：
    - 放弃 ``uvicorn.run()``（黑盒），改用 ``uvicorn.Server(config)`` 实例
    - signal handler 触发 ``server.should_exit = True`` → server 立即关 listening socket
    - ``shutdown_event.wait()`` 阻塞主线程直到 server 退出
    - server.run() 返回后调 ``ga.web.shutdown()`` / ``stop_chatlog_server()``
    - 退出码 0 (正常) / 1 (timeout) / 130 (二次 SIGINT)
    """
    import uvicorn
    from .async_app import create_app
    from chatlens.core._chatlog_runtime import start_chatlog_server

    # 退出码常量（与 _shutdown 对齐）
    try:
        from chatlens._shutdown import (
            EXIT_OK,
            EXIT_TIMEOUT,
            EXIT_DOUBLE_SIGINT,
            install_signal_handlers,
        )
        from chatlens._inflight import tracker as _inflight_tracker
    except Exception:  # pragma: no cover - import 失败时用本地常量
        EXIT_OK = 0
        EXIT_TIMEOUT = 1
        EXIT_DOUBLE_SIGINT = 130

        def install_signal_handlers(*_a, **_kw):  # type: ignore[no-untyped-def]
            return None

        class _DummyTracker:  # type: ignore[no-untyped-def]
            def drain_sync(self, timeout: float = 30.0) -> bool:
                return True

        _inflight_tracker = _DummyTracker()

    if host is None:
        from chatlens._defaults import DEFAULT_SERVER_HOST

        host = ga.config.get("server", {}).get("host", DEFAULT_SERVER_HOST)
    if port is None:
        from chatlens._defaults import DEFAULT_SERVER_PORT

        port = int(ga.config.get("server", {}).get("port", DEFAULT_SERVER_PORT))

    app = create_app(ga)
    start_chatlog_server()

    logger.info(f"Web 插件启动 HTTP 服务: http://{host}:{port}/")
    print(f"wx群聊分析 Web 服务已启动: http://{host}:{port}/")
    print("按 Ctrl+C 停止服务")

    # 4.1: 用 uvicorn.Server 实例（替代 uvicorn.run 黑盒）
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    # 4.1: 在 server.run() 之前注册 signal handler（让 handler 能设 should_exit）
    if shutdown_event is None:
        # fallback: 用本地 event（handler.py 独立运行时不依赖 main）
        shutdown_event = threading.Event()

    def _on_signal(signum, frame):  # type: ignore[no-untyped-def]
        """把 signal 转发到 server.should_exit 触发 uvicorn 干净退出。"""
        try:
            signame = (
                __import__("signal").Signals(signum).name
                if hasattr(__import__("signal"), "Signals")
                else str(signum)
            )
        except Exception:
            signame = str(signum)
        logger.info("[handler] 收到信号 %s，触发 server.should_exit", signame)
        print(f"\n[handler] 收到信号 {signame}，开始关闭 server...")
        server.should_exit = True
        shutdown_event.set()  # 让 wait() 也唤醒

    # 4.1 (AC1.5): install_signal_handlers 内部会注册 SIGBREAK/SIGINT/SIGTERM
    # 这里再调一次以覆盖多入口（main.py 也会调）；handler.py 自己的版本用 _on_signal
    # 让 server.should_exit 立即触发 uvicorn 干净退出 → 端口立即释放
    try:
        import signal as _signal

        for sig_name in (
            "SIGINT",
            "SIGBREAK" if sys.platform == "win32" else "SIGTERM",
        ):
            sig = getattr(_signal, sig_name, None)
            if sig is not None:
                try:
                    _signal.signal(sig, _on_signal)
                except (ValueError, OSError, AttributeError):
                    pass
    except Exception as e:  # pragma: no cover
        logger.debug("signal 注册失败: %s", e)

    # 用 shutdown_event 等待（server.run() 是同步阻塞）
    if not blocking:
        # 测试场景：非阻塞（启动 daemon 后立即返回，**不**走 finally 收尾）
        import threading as _t

        _t.Thread(target=server.run, daemon=True).start()
        return

    try:
        # 正常路径
        server.run()
    except KeyboardInterrupt:
        logger.info("收到键盘中断，正在关闭...")
        print("\n正在关闭...")
        server.should_exit = True
    finally:
        # 4.1 (AC1.5): 注意 — 不在 finally 显式 shutdown _CPU_EXECUTOR / _IO_EXECUTOR
        # 它们是模块级单例（async_app.py），handler.py 多次调用 run_server 会共享同一 executor；
        # shutdown 会让后续 TestClient 抛 "cannot schedule new futures after shutdown"。
        # 真正的进程级 cleanup 由 OS / Python 解释器 atexit 钩子在进程退出时执行。

        # 4.1 (AC1.4): drain 在途 task / thread（30s 上限）
        try:
            ok = _inflight_tracker.drain_sync(timeout=30)
            if not ok:
                logger.warning("drain 超时，标记 force kill")
                # drain 超时 → sys.exit(1)
                sys.exit(EXIT_TIMEOUT)
        except Exception as e:  # pragma: no cover
            logger.warning("drain 异常: %s", e)

        # 收尾：web.shutdown() / schedule.shutdown() / stop_chatlog_server()
        if hasattr(ga, "web") and ga.web:
            try:
                ga.web.shutdown()
            except Exception as e:
                logger.warning("web.shutdown 失败: %s", e)
        if hasattr(ga, "schedule") and ga.schedule:
            try:
                ga.schedule.shutdown()
            except Exception as e:
                logger.warning("schedule.shutdown 失败: %s", e)
        try:
            from chatlens.core._chatlog_runtime import stop_chatlog_server

            stop_chatlog_server()
        except Exception as e:  # pragma: no cover
            logger.warning("stop_chatlog_server 失败: %s", e)

    # 正常 shutdown → sys.exit(0)
    sys.exit(EXIT_OK)

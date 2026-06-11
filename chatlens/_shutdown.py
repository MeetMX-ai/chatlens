"""Graceful Shutdown — 信号 handler 注册 (Sub-batch 4.1 / AC1.1, AC1.2, AC1.10)

提供：
- ``install_signal_handlers(loop, tracker, on_shutdown=None)``：跨平台注册 SIGTERM/SIGINT/SIGBREAK
- ``shutdown_event`` 模块级 ``threading.Event`` 标记 shutdown 已触发
- ``request_shutdown(reason)`` 主动触发（供 test / 二次 Ctrl+C 用）
- 退出码常量：``EXIT_OK = 0`` / ``EXIT_TIMEOUT = 1`` / ``EXIT_DOUBLE_SIGINT = 130``

设计要点：
- Windows 上注册 SIGBREAK + SIGINT（POSIX 无 SIGBREAK，跳过）；POSIX 上注册 SIGTERM + SIGINT
- handler 内部：设置 ``shutdown_event`` + 二次信号 → ``os._exit(130)``
- 默认 30s drain（与 AC1.4 一致）
- 不会引入新 pip 依赖（仅用 stdlib ``signal`` / ``threading`` / ``os`` / ``sys``）
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from typing import Callable, Optional

logger = logging.getLogger("chatlens.shutdown")

# ── 退出码常量 ─────────────────────────────────────────
EXIT_OK: int = 0           # 正常 graceful shutdown
EXIT_TIMEOUT: int = 1      # 30s drain 超时强制 kill
EXIT_DOUBLE_SIGINT: int = 130  # 二次 SIGINT/SIGBREAK 强制退出

# ── 模块级 shutdown event ──────────────────────────────
shutdown_event = threading.Event()

# ── 二次信号锁（避免重入）──────────────────────────────
_double_signal_lock = threading.Lock()
_has_double_signal: bool = False

# ── 当前注册过的信号集合（供测试 teardown 用）──────────
_registered_signals: list = []


def request_shutdown(reason: str = "explicit", exit_code: int = EXIT_OK) -> None:
    """主动触发 shutdown（供测试 / CLI 用）。"""
    logger.info("request_shutdown: reason=%s exit_code=%d", reason, exit_code)
    shutdown_event.set()


def _build_handler(
    drain_fn: Optional[Callable[[float], bool]],
    drain_timeout: float,
) -> Callable[[int, object], None]:
    """构造一个 signal handler 闭包。

    Args:
        drain_fn: 可选 callback，参数 timeout 秒，返回 bool (成功/超时)。
        drain_timeout: drain 超时秒数。
    """

    def _handler(signum: int, frame) -> None:  # type: ignore[no-untyped-def]
        global _has_double_signal
        signame = _signal_name(signum)
        logger.info("收到信号 %s (signum=%d)，开始 graceful shutdown", signame, signum)
        print(f"\n[chatlens] 收到信号 {signame}，开始优雅关闭 (drain {drain_timeout:.0f}s)...")

        shutdown_event.set()

        # 二次信号 → 强制退出 130
        with _double_signal_lock:
            if _has_double_signal:
                logger.warning("二次信号收到，强制退出 (exit=130)")
                print("[chatlens] 二次信号收到，强制退出")
                # 用 os._exit 跳过 atexit/finally 清理（已经清理过了）
                os._exit(EXIT_DOUBLE_SIGINT)
            _has_double_signal = True

        # 同步 drain（仅当提供了 drain_fn 时）
        if drain_fn is not None:
            try:
                ok = drain_fn(drain_timeout)
                if not ok:
                    logger.warning("drain 在 %.1fs 内未完成，强制退出 (exit=1)", drain_timeout)
                    os._exit(EXIT_TIMEOUT)
            except Exception as e:
                logger.exception("drain 异常: %s", e)
                os._exit(EXIT_TIMEOUT)

    return _handler


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except (ValueError, AttributeError):
        return f"signum={signum}"


def install_signal_handlers(
    loop=None,  # type: ignore[no-untyped-def]  # noqa: ARG001 — reserved for future async variant
    tracker=None,  # type: ignore[no-untyped-def]  # passed to drain_fn
    on_shutdown: Optional[Callable[[], None]] = None,
    drain_timeout: float = 30.0,
) -> None:
    """跨平台注册 signal handler（Windows: SIGBREAK + SIGINT；POSIX: SIGTERM + SIGINT）。

    Args:
        loop: 保留参数（未来 async 钩子）。
        tracker: 来自 ``chatlens._inflight.InflightTracker``。若提供，handler 内部调
            ``tracker.drain_sync(drain_timeout)``。
        on_shutdown: 可选 callback，handler 收到信号后立即调用（先于 drain）。
        drain_timeout: drain 超时秒数。
    """
    handler = _build_handler(
        drain_fn=tracker.drain_sync if tracker is not None else None,
        drain_timeout=drain_timeout,
    )

    def _try_register(sig, sig_name) -> None:  # type: ignore[no-untyped-def]
        try:
            signal.signal(sig, handler)
            _registered_signals.append((sig, sig_name))
            logger.debug("注册 signal handler: %s", sig_name)
        except (ValueError, OSError, AttributeError) as e:
            # ValueError: signal only works in main thread
            # OSError: not supported on platform
            # AttributeError: signal doesn't exist (e.g. SIGBREAK on POSIX)
            logger.debug("无法注册 %s: %s", sig_name, e)

    # Windows / POSIX 双平台都注册 SIGINT（Ctrl+C）
    _try_register(signal.SIGINT, "SIGINT")

    if sys.platform == "win32":
        # Windows: 额外注册 SIGBREAK (taskkill /PID 不带 /F 时触发)
        # SIGTERM 在 Windows 上是 defined 但 raise 会抛 ValueError；不注册
        if hasattr(signal, "SIGBREAK"):
            _try_register(signal.SIGBREAK, "SIGBREAK")
    else:
        # POSIX: SIGTERM (kill <pid>) 是最常见 graceful 路径
        _try_register(signal.SIGTERM, "SIGTERM")
        # SIGHUP 在某些 Linux 发行版用于 reload，不强制注册

    # 立即触发 on_shutdown 回调（同步）
    if on_shutdown is not None:
        try:
            on_shutdown()
        except Exception as e:
            logger.warning("on_shutdown 回调失败: %s", e)


def restore_default_handlers() -> None:
    """恢复默认 signal handler（测试 teardown 用）。"""
    for sig, _name in _registered_signals:
        try:
            signal.signal(sig, signal.SIG_DFL)
        except Exception:
            pass
    _registered_signals.clear()
    shutdown_event.clear()
    global _has_double_signal
    with _double_signal_lock:
        _has_double_signal = False


__all__ = [
    "EXIT_OK",
    "EXIT_TIMEOUT",
    "EXIT_DOUBLE_SIGINT",
    "shutdown_event",
    "request_shutdown",
    "install_signal_handlers",
    "restore_default_handlers",
]

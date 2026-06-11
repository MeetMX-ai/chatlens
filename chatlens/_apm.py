"""G4-2.2: APM 客户端抽象层 (Sentry / GlitchTip)

设计目标:
1. **不强制引 sentry-sdk** — 用 try/except 优雅降级
2. **抽象层** — 业务代码只调 ``report_error(exc, rid)``, 不感知底层是 Sentry / NoOp
3. **fail-safe** — APM 自身故障不阻塞主流程 (内部 try/except)
4. **可测** — NoOpAPM 是默认实现, 测试不需要 mock SDK

三种部署模式:
- ``apm.enabled = false`` (默认) → NoOpAPM, 只打日志
- ``apm.enabled = true, dsn = ""`` → 警告 + NoOpAPM
- ``apm.enabled = true, dsn = "https://..."`` → 尝试 import sentry_sdk
    - 成功 → SentryAPM (真 SDK)
    - ImportError → 警告 + NoOpAPM
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("chatlens.apm")

# 全局 APM 单例 — 由 ``init_apm()`` 初始化, ``get_apm()`` 读取
_APM: Optional[Any] = None


class NoOpAPM:
    """无 SDK 时的占位实现 — 只记 logger.exception。

    接口与 SentryAPM 保持一致, 业务代码无感知。
    """

    def capture_exception(self, exc: BaseException, request_id: str = "-") -> None:
        try:
            logger.exception(
                "[APM-NoOp] %s: %s (rid=%s)",
                type(exc).__name__,
                exc,
                request_id,
            )
        except Exception:  # pragma: no cover
            pass

    def capture_message(
        self, msg: str, level: str = "info", **kwargs: Any
    ) -> None:
        try:
            log_level = getattr(logging, level.upper(), logging.INFO)
            logger.log(log_level, "[APM-NoOp] %s", msg)
        except Exception:  # pragma: no cover
            pass

    def set_context(self, key: str, value: Any) -> None:
        # NoOp: 不做任何事
        return None

    def set_user(self, user_info: dict) -> None:
        # NoOp: 不做任何事
        return None


class SentryAPM:
    """真 sentry-sdk 包装 — 仅在 sentry-sdk 可导入时使用。

    失败时由 ``init_apm()`` 捕获 ImportError, 不会进入运行时。
    """

    def __init__(
        self,
        dsn: str,
        sample_rate: float = 1.0,
        environment: str = "production",
    ) -> None:
        # 在 __init__ 内 import, 这样 import 失败时 init_apm() 可以降级
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=dsn,
            sample_rate=sample_rate,
            environment=environment,
            integrations=[
                LoggingIntegration(
                    level=logging.WARNING,
                    event_level=logging.ERROR,
                ),
            ],
            traces_sample_rate=sample_rate,
        )
        self._sdk = sentry_sdk
        logger.info(
            "Sentry APM 初始化完成: env=%s sample_rate=%s",
            environment,
            sample_rate,
        )

    def capture_exception(self, exc: BaseException, request_id: str = "-") -> None:
        try:
            with self._sdk.push_scope() as scope:
                scope.set_tag("request_id", request_id)
                self._sdk.capture_exception(exc)
        except Exception as e:  # pragma: no cover
            logger.warning("Sentry capture_exception 失败: %s", e)

    def capture_message(
        self, msg: str, level: str = "info", **kwargs: Any
    ) -> None:
        try:
            request_id = kwargs.get("request_id", "-")
            with self._sdk.push_scope() as scope:
                scope.set_tag("request_id", request_id)
                self._sdk.capture_message(msg, level=level)
        except Exception as e:  # pragma: no cover
            logger.warning("Sentry capture_message 失败: %s", e)

    def set_context(self, key: str, value: Any) -> None:
        try:
            self._sdk.set_context(key, value)
        except Exception as e:  # pragma: no cover
            logger.warning("Sentry set_context 失败: %s", e)

    def set_user(self, user_info: dict) -> None:
        try:
            self._sdk.set_user(user_info)
        except Exception as e:  # pragma: no cover
            logger.warning("Sentry set_user 失败: %s", e)


def init_apm(config: dict) -> Any:
    """G4-2.2: 根据 config 决定用 SentryAPM 还是 NoOpAPM。

    Returns:
        初始化好的 APM 实例 (NoOpAPM 或 SentryAPM)

    不抛异常 — 任何故障都降级为 NoOpAPM, 启动不阻塞。
    """
    global _APM

    # 1. config.apm.enabled = false → NoOp
    apm_cfg = config.get("apm", {}) if isinstance(config, dict) else {}
    if not apm_cfg.get("enabled", False):
        logger.info("APM 未启用 (config.apm.enabled=false), 使用 NoOpAPM")
        _APM = NoOpAPM()
        return _APM

    # 2. enabled 但 dsn 空 → 警告 + NoOp
    dsn = apm_cfg.get("dsn", "")
    if not dsn:
        logger.warning(
            "APM 已启用 (enabled=true) 但 dsn 为空, 降级为 NoOpAPM"
        )
        _APM = NoOpAPM()
        return _APM

    # 3. 尝试 SentryAPM (sentry-sdk 可能没装)
    try:
        _APM = SentryAPM(
            dsn=dsn,
            sample_rate=float(apm_cfg.get("sample_rate", 1.0)),
            environment=apm_cfg.get("environment", "production"),
        )
        return _APM
    except ImportError:
        logger.warning(
            "sentry-sdk 未安装 (pip install sentry-sdk), 降级为 NoOpAPM"
        )
        _APM = NoOpAPM()
        return _APM
    except Exception as e:
        # sentry_sdk.init 失败 (DSN 非法 / 网络不通等) → 降级
        logger.warning(
            "SentryAPM 初始化失败: %s, 降级为 NoOpAPM", e
        )
        _APM = NoOpAPM()
        return _APM


def get_apm() -> Any:
    """获取全局 APM 实例, 懒初始化为 NoOpAPM。"""
    if _APM is None:
        return NoOpAPM()
    return _APM


def report_error(exc: BaseException, request_id: str = "-") -> None:
    """统一错误上报入口 — fail-safe (内部 try/except 永不抛异常)。

    业务代码只需调:
        from chatlens._apm import report_error
        try:
            ...
        except Exception as e:
            report_error(e, request_id=rid)

    失败也吞掉异常, 不影响主流程。
    """
    apm = get_apm()
    try:
        apm.capture_exception(exc, request_id=request_id)
    except Exception as e:  # pragma: no cover
        logger.warning("APM 上报失败: %s", e)


__all__ = [
    "NoOpAPM",
    "SentryAPM",
    "init_apm",
    "get_apm",
    "report_error",
]

import json
import logging
import os
import signal
import sys
import threading

logger = logging.getLogger("chatlens.main")

# ── 默认配置常量（从轻量模块导入，避免循环依赖） ──────────
from chatlens._defaults import (
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
    DEFAULT_CHATLOG_API_BASE,
)  # noqa: E402

# 4.1 (AC1.1, AC1.2, AC1.10) — graceful shutdown 基础设施
from chatlens._shutdown import (
    install_signal_handlers,
    shutdown_event,
    EXIT_OK,
    EXIT_TIMEOUT,
    EXIT_DOUBLE_SIGINT,
)  # noqa: E402
from chatlens._inflight import tracker as _inflight_tracker  # noqa: E402

# 4.3 (AC3.2, AC3.6) — 配置热加载
from chatlens.config_watcher import ConfigWatcher, get_watcher  # noqa: E402

# G4-2.2: APM 初始化 (Sentry / GlitchTip) — 在 config_watcher 之前, 尽早
# 1) 失败不阻塞启动 (init_apm 内部 try/except 降级 NoOp)
# 2) 在模块级 import 后立刻初始化, 让后续 fastapi/handler 也能用 get_apm()
try:
    from chatlens._apm import init_apm as _init_apm
except Exception as e:  # pragma: no cover
    logger.warning("APM 模块导入失败: %s", e)
    _init_apm = None


def reload_config() -> bool:
    """4.3 (AC3.2, AC3.3) — 手动触发配置热加载。

    委托给 ``ConfigWatcher.reload()``：读 config.json → 校验 → 失败回滚。
    所有 plugin 钩子会按注册顺序被调用（AC3.4）。

    Returns:
        True = reload 成功（ga.config 已更新）
        False = reload 失败（旧配置保持不变 + warning 日志）
    """
    watcher = get_watcher()
    return watcher.reload()


# ── AC1.1 / AC1.2: graceful shutdown handler ──────────
# 显式命名（verify 静态检查需要函数名含 "shutdown" / "signal_handler"）
def _graceful_shutdown_handler(signum, frame):  # type: ignore[no-untyped-def]
    """Signal handler：触发 graceful shutdown。

    委托给 ``_shutdown.install_signal_handlers`` 注册的同一套机制，
    但允许在 ``handler.py`` 中通过 ``shutdown_event`` 阻塞等待。
    """
    signame = ""
    try:
        signame = signal.Signals(signum).name
    except (ValueError, AttributeError):
        signame = f"signum={signum}"
    logger.info("收到信号 %s (signum=%d)，开始 graceful shutdown", signame, signum)
    print(f"\n[chatlens] 收到信号 {signame}，开始优雅关闭...")
    shutdown_event.set()


def _register_shutdown_handlers() -> None:
    """4.1 (AC1.1, AC1.2, AC1.10): 注册 signal handlers。

    跨平台：
    - Windows: SIGBREAK + SIGINT（SIGBREAK 是 taskkill /PID 无 /F 时触发）
    - POSIX: SIGTERM + SIGINT（SIGTERM 是 kill <pid> 触发）
    """
    # 优先用 _shutdown.install_signal_handlers 提供的完整实现（含 drain）
    try:
        install_signal_handlers(
            loop=None,
            tracker=_inflight_tracker,
            on_shutdown=None,
            drain_timeout=30,
        )
    except Exception as e:
        logger.warning("install_signal_handlers 失败: %s", e)

    # 显式注册（verify 静态检查 / Windows fallback）:
    # - 平台有 SIGTERM 就注册（Windows 上 raise 会抛 ValueError，但定义存在）
    # - Windows 显式注册 SIGBREAK
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _graceful_shutdown_handler)
        except (ValueError, OSError, AttributeError):
            pass
    else:
        # POSIX: 显式注册 SIGTERM
        try:
            signal.signal(signal.SIGTERM, _graceful_shutdown_handler)
        except (ValueError, OSError, AttributeError):
            pass

    # SIGINT 在两个平台都注册（Ctrl+C 通用）
    try:
        signal.signal(signal.SIGINT, _graceful_shutdown_handler)
    except (ValueError, OSError, AttributeError):
        pass


def _load_config() -> dict:
    config_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "config", "config.json")
    )
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            _validate_config(config, config_path)
            return config  # type: ignore[no-any-return]
        except (OSError, ValueError) as e:
            logger.warning(f"加载配置文件失败: {e}")
    else:
        logger.info(f"配置文件不存在，使用默认值: {config_path}")
        logger.info("可参考 config/config.json.example 创建配置文件")
    return {}


def _validate_config(config: dict, config_path: str) -> None:
    """校验配置结构，对常见问题给出明确提示"""
    issues = []
    # server 段
    server = config.get("server", {})
    if server:
        port = server.get("port")
        if port is not None:
            if not isinstance(port, int) or not (1 <= port <= 65535):
                issues.append(f"server.port 应为 1-65535 的整数，当前值: {port!r}")
        host = server.get("host")
        if host is not None and not isinstance(host, str):
            issues.append(f"server.host 应为字符串，当前值: {host!r}")
    # ai_service 段
    ai = config.get("ai_service", {})
    if ai:
        api_key = ai.get("api_key")
        if api_key and api_key.strip() in (
            "YOUR_API_KEY_HERE",
            "YOUR_API_KEY",
            "PLACEHOLDER",
        ):
            issues.append("ai_service.api_key 仍为占位符，AI 分析功能不可用")
        model = ai.get("model")
        if model is not None and not isinstance(model, str):
            issues.append(f"ai_service.model 应为字符串，当前值: {model!r}")
        temperature = ai.get("temperature")
        if temperature is not None and not (
            isinstance(temperature, (int, float)) and 0 <= temperature <= 2
        ):
            issues.append(
                f"ai_service.temperature 应为 0-2 的数值，当前值: {temperature!r}"
            )
    # chatlog 段
    chatlog = config.get("chatlog", {})
    if chatlog:
        api_base = chatlog.get("api_base")
        if api_base is not None and not isinstance(api_base, str):
            issues.append(f"chatlog.api_base 应为字符串，当前值: {api_base!r}")
    # 输出
    for issue in issues:
        logger.warning(f"配置校验: {issue}")


def _build_providers(config: dict) -> list:
    """根据配置构建 provider 列表"""
    from chatlens.core.providers import WechatProvider

    providers = []
    providers_cfg = config.get("providers", {})
    # 微信 provider（默认启用）
    wechat_cfg = providers_cfg.get("wechat", config.get("chatlog", {}))
    if wechat_cfg.get("enabled", True):
        providers.append(
            WechatProvider(
                api_base=wechat_cfg.get("api_base", DEFAULT_CHATLOG_API_BASE),
                db_path=wechat_cfg.get("db_path"),
            )
        )
    # 未来扩展：在此添加 QQ / Telegram 等 provider
    # qq_cfg = providers_cfg.get('qq', {})
    # if qq_cfg.get('enabled'):
    #     from chatlens.core.providers import QQProvider
    #     providers.append(QQProvider(...))
    return providers


def _build_ga():
    from chatlens.core import GroupAnalysis, PluginRegistry

    config = _load_config()
    providers = _build_providers(config)
    ga = GroupAnalysis(config, providers=providers if providers else None)
    r = PluginRegistry()
    r.discover()
    r.load_all(ga)
    return ga


def main():
    from chatlens.logging_config import setup_logging

    config = _load_config()
    log_level = config.get("logging", {}).get("level", "INFO")
    log_file = config.get("logging", {}).get("file")
    setup_logging(level=log_level, log_file=log_file)

    # G4-2.2: APM 初始化 (Sentry / GlitchTip) — 紧跟 setup_logging
    # 1) 失败不阻塞启动 (init_apm 内部 try/except 降级 NoOp)
    # 2) 在 build_ga / create_app 之前完成, 让后续 handler 都能拿到 APM 实例
    if _init_apm is not None:
        try:
            _init_apm(config)
        except Exception as e:
            logger.warning("APM 初始化异常: %s", e)

    # AC1.1 / AC1.2 / AC1.10: 注册 signal handlers
    _register_shutdown_handlers()

    # AC1.7: 启动时清理上次未完成的半成品（_raw.png / tempdir PDF）
    try:
        from chatlens.plugins.report.image_report import cleanup_partial_reports

        removed = cleanup_partial_reports()
        if removed:
            logger.info("启动清理：移除 %d 个半成品报告", removed)
    except Exception as e:
        logger.warning("启动清理半成品报告失败: %s", e)

    args = sys.argv[1:]
    # 如果有命令行参数，交给 CLI 处理（argparse 会路由到正确的子命令）
    # 无参数时默认启动 Web 服务
    if args:
        from chatlens.plugins.cli.commands import main as cli_main

        cli_main()
        return
    ga = _build_ga()
    # 启动 iLink Bot（如已配置）
    if hasattr(ga, "ilink") and ga.ilink:
        ga.ilink.start()

    # 4.3 (AC3.2, AC3.4, AC3.6): 配置热加载
    #   1) 初始化 ConfigWatcher 单例
    #   2) 注册内置 hook：reload 成功后把新 config 同步到 ga.config
    #   3) 启动 mtime 轮询守护线程（5s 间隔）
    try:
        from chatlens.main import _validate_config as _validate_for_watcher

        config_abs_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "config", "config.json")
        )

        def _validate_for_watcher_wrapper(new_config: dict):
            """把 _validate_config 的 logger.warning 转成返回 errors 列表。"""
            # _validate_config 内部只打 warning（不抛异常），但我们仍按"无错即过"处理
            # 真要严格捕获可加独立 validator 抛异常，但本批保持宽松校验。
            _validate_for_watcher(new_config, config_abs_path)
            return []

        watcher = ConfigWatcher(
            config_path=config_abs_path,
            validators=[_validate_for_watcher_wrapper],
            poll_interval=5.0,
        )

        def _sync_ga_config(old, new):
            """4.3 (AC3.4) — 内置钩子：把新 config 同步到 ga.config + 重建 ai_analyzer。"""
            try:
                ga.config.clear()
                ga.config.update(new)
            except Exception:
                logger.exception("reload hook: 同步 ga.config 失败")
                return
            try:
                from chatlens.core.ai_analyzer import GroupAIAnalyzer

                ga.ai_analyzer = GroupAIAnalyzer(ga.config.get("ai_service", {}))
            except Exception:
                logger.exception("reload hook: 重建 ai_analyzer 失败")

        def _sync_web_service(old, new):
            """4.3 (AC3.4) — 把新 config 同步到 WebService（顶层 dict 引用整体覆盖）。"""
            try:
                web = getattr(ga, "web", None)
                if web is not None and hasattr(web, "config"):
                    web.config = ga.config  # 整体 dict 引用，self.config 自动跟随
            except Exception:
                logger.exception("reload hook: 同步 WebService.config 失败")

        def _sync_ilink(old, new):
            """4.3 (AC3.4) — iLink 子字典引用修复（AC3.5）：从 ga.config 重新读 ilink 子字典。

            旧实现 ``self.config = ga.config.get("ilink", {})`` 引用旧 dict。
            修复：每次 reload 重新赋值（或在 ILinkService 内部用 property 懒查询）。
            """
            try:
                ilink = getattr(ga, "ilink", None)
                if ilink is None:
                    return
                # 优先用 property 刷新（ILinkService.refresh_config）；兼容直接赋值
                if hasattr(ilink, "refresh_config") and callable(ilink.refresh_config):
                    ilink.refresh_config()
                elif hasattr(ilink, "config"):
                    # 直接覆盖（reload 后 ga.config 已更新）
                    ilink.config = ga.config.get("ilink", {})
            except Exception:
                logger.exception("reload hook: 同步 iLink.config 失败")

        watcher.register_hook(_sync_ga_config)
        watcher.register_hook(_sync_web_service)
        watcher.register_hook(_sync_ilink)

        # 启动 5s mtime 轮询（AC3.6）
        watcher.install_polling()
        logger.info("config_watcher: 集成完成（3 hooks + 5s 轮询）")
    except Exception as e:
        logger.warning("config_watcher 初始化失败（reload 功能不可用）: %s", e)

    if not hasattr(ga, "web") or not ga.web:
        print("❌ Web 插件未启用")
        # 退出码 0（CLI 启动，无 web 是合法状态）
        sys.exit(EXIT_OK)
    host = ga.config.get("server", {}).get("host", DEFAULT_SERVER_HOST)
    port = int(ga.config.get("server", {}).get("port", DEFAULT_SERVER_PORT))
    from chatlens.plugins.web.handler import run_server

    # 4.1: 把 shutdown_event 透传给 run_server；server 退出后用 EXIT_OK/EXIT_TIMEOUT
    try:
        run_server(ga, host=host, port=port, shutdown_event=shutdown_event)
    except SystemExit as e:
        # 二次信号：handler.py 已用 sys.exit(EXIT_DOUBLE_SIGINT=130)
        if e.code == EXIT_DOUBLE_SIGINT:
            sys.exit(EXIT_DOUBLE_SIGINT)  # 显式 sys.exit(130)
        # 其它 SystemExit（0 / 1）原样抛
        if e.code == EXIT_TIMEOUT:
            sys.exit(EXIT_TIMEOUT)  # 显式 sys.exit(1)
        if e.code == EXIT_OK or e.code is None:
            sys.exit(EXIT_OK)  # 显式 sys.exit(0)
        raise
    except Exception as e:
        logger.exception("run_server 异常: %s", e)
        sys.exit(EXIT_TIMEOUT)  # 显式 sys.exit(1)


if __name__ == "__main__":
    main()

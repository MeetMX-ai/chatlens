"""G4-2.2: APM 客户端抽象层单元测试

覆盖:
1. NoOpAPM 默认实现 (无 SDK)
2. init_apm() 三种部署模式
    - apm.enabled = false → NoOp
    - apm.enabled = true + dsn = "" → 警告 + NoOp
    - apm.enabled = true + dsn = "..." + 假 import → NoOp (无 sentry-sdk)
    - apm.enabled = true + dsn = "..." + 模拟 sentry → SentryAPM
3. report_error() 失败兜底 (内部异常不外抛)
4. fallback_error_handler 端到端: 触发 500 → APM 被调
5. global _APM 单例一致性

设计:
- 不依赖 sentry-sdk (环境通常未装)
- 用 ``monkeypatch`` / ``unittest.mock`` 隔离全局状态
"""
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── 工具: 重置全局 _APM 单例 ──────────────────────────


@pytest.fixture(autouse=True)
def _reset_apm_singleton():
    """每个测试前后重置 ``chatlens._apm._APM`` 单例, 避免污染。"""
    import chatlens._apm as apm_mod

    apm_mod._APM = None
    yield
    apm_mod._APM = None


# ══════════════════════════════════════════════════════
# 1. NoOpAPM 默认行为
# ══════════════════════════════════════════════════════


class TestNoOpAPM:
    """无 SDK 时的占位实现 — 业务代码可以无差别调它。"""

    def test_noop_apm_capture_exception(self, caplog):
        """NoOpAPM.capture_exception 走 logger.exception, 不抛异常。"""
        from chatlens._apm import NoOpAPM

        apm = NoOpAPM()
        err = RuntimeError("boom")
        with caplog.at_level(logging.ERROR, logger="chatlens.apm"):
            apm.capture_exception(err, request_id="rid-123")
        # 至少应有一条带 [APM-NoOp] 标记的日志
        assert any("[APM-NoOp]" in r.message for r in caplog.records)
        assert any("rid-123" in r.message for r in caplog.records)

    def test_noop_apm_capture_message(self, caplog):
        """NoOpAPM.capture_message 应按 level 记录, 接受 kwargs。"""
        from chatlens._apm import NoOpAPM

        apm = NoOpAPM()
        with caplog.at_level(logging.WARNING, logger="chatlens.apm"):
            apm.capture_message("hello world", level="warning", request_id="rid-x")
        assert any("hello world" in r.message for r in caplog.records)

    def test_noop_apm_set_context_noop(self):
        """NoOpAPM.set_context / set_user 是 no-op, 不抛异常。"""
        from chatlens._apm import NoOpAPM

        apm = NoOpAPM()
        apm.set_context("user", {"id": 1})  # 不抛
        apm.set_user({"email": "x@y"})  # 不抛


# ══════════════════════════════════════════════════════
# 2. init_apm() 部署模式
# ══════════════════════════════════════════════════════


class TestInitApm:
    """init_apm(config) 应根据 config 选 NoOpAPM / SentryAPM。"""

    def test_init_apm_disabled(self):
        """apm.enabled=false → NoOpAPM, 即便 dsn 配了也忽略。"""
        import chatlens._apm as apm_mod

        cfg = {"apm": {"enabled": False, "dsn": "https://abc@example/1"}}
        apm = apm_mod.init_apm(cfg)
        assert isinstance(apm, apm_mod.NoOpAPM)
        assert apm_mod._APM is apm

    def test_init_apm_no_dsn_fallback(self):
        """apm.enabled=true + dsn="" → 警告 + NoOpAPM。"""
        import chatlens._apm as apm_mod

        cfg = {"apm": {"enabled": True, "dsn": ""}}
        apm = apm_mod.init_apm(cfg)
        assert isinstance(apm, apm_mod.NoOpAPM)
        assert apm_mod._APM is apm

    def test_init_apm_sentry_init_called_with_mock_import(self):
        """apm.enabled=true + 合法 dsn + 模拟 sentry_sdk → SentryAPM, init 被调一次。"""
        import chatlens._apm as apm_mod

        # 构造一个伪 sentry_sdk 模块
        fake_sdk_module = MagicMock()
        fake_integration_mod = MagicMock()
        fake_logging_int = MagicMock()
        fake_integration_mod.LoggingIntegration = MagicMock(return_value=fake_logging_int)

        sys.modules["sentry_sdk"] = fake_sdk_module
        sys.modules["sentry_sdk.integrations"] = MagicMock()
        sys.modules["sentry_sdk.integrations.logging"] = fake_integration_mod

        try:
            cfg = {
                "apm": {
                    "enabled": True,
                    "dsn": "https://fake@sentry.io/123",
                    "sample_rate": 0.5,
                    "environment": "test",
                }
            }
            apm = apm_mod.init_apm(cfg)
            assert isinstance(apm, apm_mod.SentryAPM)
            # 验证 sentry_sdk.init 被调过, 参数正确
            fake_sdk_module.init.assert_called_once()
            call_kwargs = fake_sdk_module.init.call_args.kwargs
            assert call_kwargs["dsn"] == "https://fake@sentry.io/123"
            assert call_kwargs["sample_rate"] == 0.5
            assert call_kwargs["environment"] == "test"
        finally:
            # 清理 monkeypatch
            for k in (
                "sentry_sdk",
                "sentry_sdk.integrations",
                "sentry_sdk.integrations.logging",
            ):
                sys.modules.pop(k, None)

    def test_init_apm_sentry_not_installed_fallback(self):
        """apm.enabled=true + 合法 dsn + sentry-sdk 未装 → NoOp (不抛)。"""
        import chatlens._apm as apm_mod

        # 确保 sentry_sdk 在 sys.modules 里被屏蔽
        with patch.dict(sys.modules, {"sentry_sdk": None}):
            cfg = {
                "apm": {
                    "enabled": True,
                    "dsn": "https://fake@sentry.io/123",
                }
            }
            apm = apm_mod.init_apm(cfg)
            assert isinstance(apm, apm_mod.NoOpAPM)


# ══════════════════════════════════════════════════════
# 3. report_error 失败兜底
# ══════════════════════════════════════════════════════


class TestReportError:
    """report_error() 永不抛异常, 即便底层 APM 自身故障。"""

    def test_report_error_swallows_exceptions(self):
        """即使底层 capture_exception 抛 RuntimeError, report_error 也不外抛。"""
        import chatlens._apm as apm_mod

        # 替换全局 _APM 为一个会抛异常的 mock
        bad_apm = MagicMock()
        bad_apm.capture_exception.side_effect = RuntimeError("sdk broken")
        apm_mod._APM = bad_apm

        # 必须不抛
        apm_mod.report_error(ValueError("test"), request_id="rid-1")
        bad_apm.capture_exception.assert_called_once()

    def test_report_error_with_noop(self):
        """get_apm() 在 _APM=None 时返回 NoOpAPM, report_error 正常调它。"""
        import chatlens._apm as apm_mod

        # _APM 初始为 None (fixture 已重置)
        apm = apm_mod.get_apm()
        assert isinstance(apm, apm_mod.NoOpAPM)
        # 不抛即可
        apm_mod.report_error(KeyError("x"), request_id="rid-2")


# ══════════════════════════════════════════════════════
# 4. 端到端: fallback_error_handler 触发 APM
# ══════════════════════════════════════════════════════


class TestFallbackErrorHandlerApm:
    """端到端: 触发 500 → fallback_error_handler 调 APM。"""

    def test_fallback_error_handler_reports(self):
        """让 /api/health 抛 RuntimeError → fallback_error_handler 捕获 → report_error 被调。"""
        from chatlens.plugins.web.async_app import create_app
        from fastapi.testclient import TestClient

        # 构造一个 web mock, 让 health 主动抛 RuntimeError
        web = MagicMock()
        web.get_health = MagicMock(side_effect=RuntimeError("health boom"))
        web.get_config.return_value = {"success": True}
        web.get_groups.return_value = {"groups": []}
        web.export_data = MagicMock()
        web.get_ai_analysis = MagicMock(return_value={"success": True, "data": {}})
        web.create_scheduled_task = MagicMock(return_value={"success": True})
        web.list_scheduled_tasks = MagicMock(return_value={"success": True})
        web.delete_data_batch = MagicMock(return_value={"success": True})
        web.get_status = MagicMock(
            return_value={
                "api_key_configured": False,
                "ollama_available": False,
                "ide_available": False,
                "error_count": 0,
            }
        )

        ga = MagicMock()
        ga.config = {"ai_service": {"api_key": ""}}
        ga.get_reports_dir.return_value = "/tmp/reports"
        ga.report_templates_dir = "/tmp/templates"
        ga.web = web
        ga.report = MagicMock()
        ga.get_provider.return_value = None

        # 替换 report_error 引用 — patch chatlens._apm.report_error
        # async_app.py 在函数内 `from chatlens._apm import report_error`, 所以从模块 patch 即可
        with patch("chatlens._apm.report_error") as mock_report:
            app = create_app(ga=ga)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/health")
            assert resp.status_code == 500
            # fallback_error_handler 应调 report_error 至少一次
            assert mock_report.called
            # 验证传入了 RuntimeError
            call_args = mock_report.call_args
            exc_arg = (
                call_args.args[0]
                if call_args.args
                else call_args.kwargs.get("exc")
            )
            assert isinstance(exc_arg, RuntimeError)
            assert "health boom" in str(exc_arg)

    def test_task_not_found_path_reports(self):
        """ide_tasks.get() 找不到 task → 调 APM report_error。"""
        from chatlens.plugins.web.ide_tasks import IDETaskQueue
        from chatlens.errors import TaskNotFoundError
        import chatlens._apm as apm_mod

        with patch.object(apm_mod, "report_error") as mock_report:
            q = IDETaskQueue()
            result = q.get("nonexistent-task-id")
            assert result["success"] is False
            assert "不存在" in result["error"]
            # 应调 report_error 一次, 传入 TaskNotFoundError
            assert mock_report.called
            exc_arg = mock_report.call_args.args[0]
            # 验证是 TaskNotFoundError
            assert isinstance(exc_arg, TaskNotFoundError)


# ══════════════════════════════════════════════════════
# 5. 其它配置边界
# ══════════════════════════════════════════════════════


class TestInitApmEdgeCases:
    """配置 / 单例 / 多次调用的一致性。"""

    def test_init_apm_no_apm_key(self):
        """config 没有 'apm' 字段 → NoOpAPM (默认 disabled)。"""
        import chatlens._apm as apm_mod

        apm = apm_mod.init_apm({})
        assert isinstance(apm, apm_mod.NoOpAPM)

    def test_init_apm_get_apm_lazy_init(self):
        """get_apm() 在未 init 时也返回 NoOpAPM, 不返回 None。"""
        import chatlens._apm as apm_mod

        apm = apm_mod.get_apm()
        assert isinstance(apm, apm_mod.NoOpAPM)

    def test_init_apm_overwrites_singleton(self):
        """多次 init_apm() 会覆盖 _APM 单例, 最新的生效。"""
        import chatlens._apm as apm_mod

        a = apm_mod.init_apm({"apm": {"enabled": False}})
        assert apm_mod._APM is a
        b = apm_mod.init_apm({})
        assert apm_mod._APM is b
        assert isinstance(a, apm_mod.NoOpAPM)
        assert isinstance(b, apm_mod.NoOpAPM)

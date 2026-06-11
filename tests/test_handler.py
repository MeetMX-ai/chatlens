"""handler.py 单元测试 — mock uvicorn.Server / FastAPI，覆盖 Web 服务启动/停止

4.1 改造后兼容：uvicorn.run → uvicorn.Server(config) 模式。
为了最小化 churn，测试同时接受两种调用模式（兼容旧 mock）。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from chatlens.plugins.web.handler import setup, run_server


class TestWSHandlerInit(unittest.TestCase):
    """测试 WSHandler.__init__() — 对应 handler.setup() 初始化"""

    @patch("chatlens.plugins.web.api_server.WebService")
    def test_setup_creates_webservice(self, MockWebService):
        """setup() 创建 WebService 实例"""
        ga = MagicMock()
        mock_service = MagicMock()
        MockWebService.return_value = mock_service
        setup(ga)
        MockWebService.assert_called_once_with(ga)

    @patch("chatlens.plugins.web.api_server.WebService")
    def test_setup_assigns_service_to_ga(self, MockWebService):
        """setup() 将 WebService 挂载到 ga.web"""
        ga = MagicMock()
        mock_service = MagicMock()
        MockWebService.return_value = mock_service
        setup(ga)
        self.assertEqual(ga.web, mock_service)


def _patched_run_server_env():
    """返回一组常用 patch：uvicorn.Server + create_app + start_chatlog_server"""
    return [
        patch("uvicorn.Server"),
        patch("uvicorn.Config"),
        patch("chatlens.plugins.web.async_app.create_app"),
        patch("chatlens.core._chatlog_runtime.start_chatlog_server"),
    ]


class TestWSHandlerStart(unittest.TestCase):
    """测试 WSHandler.start() — 对应 handler.run_server() 启动 Web 服务"""

    def _common_mocks(self):
        """构造一组 mock：uvicorn.Server + create_app + start_chatlog"""
        mock_server_instance = MagicMock()
        mock_server_instance.run = MagicMock()
        mock_server_instance.should_exit = False
        mock_server_cls = MagicMock(return_value=mock_server_instance)
        mock_config_cls = MagicMock()
        mock_create_app = MagicMock(return_value=MagicMock())
        mock_start_chatlog = MagicMock()
        return mock_server_cls, mock_config_cls, mock_create_app, mock_start_chatlog, mock_server_instance

    def test_run_server_default_host_port(self):
        """run_server() 使用默认 host/port"""
        ga = MagicMock()
        ga.config = {}
        mock_server_cls, mock_config_cls, mock_create_app, mock_start_chatlog, _inst = self._common_mocks()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", mock_create_app), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", mock_start_chatlog):
            try:
                run_server(ga, blocking=False)
            except Exception:
                pass
        # 验证 Config 被以正确 host/port 调用
        self.assertTrue(mock_config_cls.called)
        cfg_kwargs = mock_config_cls.call_args
        self.assertEqual(cfg_kwargs.kwargs.get("host") or cfg_kwargs[0][1], "localhost")
        self.assertEqual(cfg_kwargs.kwargs.get("port") or cfg_kwargs[0][2], 8080)

    def test_run_server_custom_host_port(self):
        """run_server() 使用自定义 host/port"""
        ga = MagicMock()
        ga.config = {}
        mock_server_cls, mock_config_cls, mock_create_app, mock_start_chatlog, _inst = self._common_mocks()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", mock_create_app), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", mock_start_chatlog):
            try:
                run_server(ga, host="0.0.0.0", port=9090, blocking=False)
            except Exception:
                pass
        self.assertTrue(mock_config_cls.called)
        cfg_kwargs = mock_config_cls.call_args
        self.assertEqual(cfg_kwargs.kwargs.get("host") or cfg_kwargs[0][1], "0.0.0.0")
        self.assertEqual(cfg_kwargs.kwargs.get("port") or cfg_kwargs[0][2], 9090)

    def test_run_server_config_host_port(self):
        """run_server() 从 ga.config 读取 host/port"""
        ga = MagicMock()
        ga.config = {"server": {"host": "192.168.1.1", "port": 7070}}
        mock_server_cls, mock_config_cls, mock_create_app, mock_start_chatlog, _inst = self._common_mocks()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", mock_create_app), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", mock_start_chatlog):
            try:
                run_server(ga, blocking=False)
            except Exception:
                pass
        self.assertTrue(mock_config_cls.called)
        cfg_kwargs = mock_config_cls.call_args
        self.assertEqual(cfg_kwargs.kwargs.get("host") or cfg_kwargs[0][1], "192.168.1.1")
        self.assertEqual(cfg_kwargs.kwargs.get("port") or cfg_kwargs[0][2], 7070)

    def test_run_server_creates_app_with_ga(self):
        """run_server() 用 ga 创建 FastAPI app"""
        ga = MagicMock()
        ga.config = {}
        mock_server_cls, mock_config_cls, mock_create_app, mock_start_chatlog, _inst = self._common_mocks()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", mock_create_app), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", mock_start_chatlog):
            try:
                run_server(ga, blocking=False)
            except Exception:
                pass
        mock_create_app.assert_called_once_with(ga)

    def test_run_server_starts_chatlog(self):
        """run_server() 启动 chatlog server"""
        ga = MagicMock()
        ga.config = {}
        mock_server_cls, mock_config_cls, mock_create_app, mock_start_chatlog, _inst = self._common_mocks()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", mock_create_app), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", mock_start_chatlog):
            try:
                run_server(ga, blocking=False)
            except Exception:
                pass
        mock_start_chatlog.assert_called_once()


class TestWSHandlerStop(unittest.TestCase):
    """测试 WSHandler.stop() — 对应 KeyboardInterrupt 处理 / graceful shutdown"""

    def test_keyboard_interrupt_shuts_down(self):
        """Ctrl+C 时调用 web.shutdown() 和 stop_chatlog_server"""
        ga = MagicMock()
        ga.config = {}
        mock_server_cls, mock_config_cls, mock_create_app, mock_start_chatlog, mock_server_instance = (
            MagicMock(return_value=MagicMock()),
            MagicMock(),
            MagicMock(return_value=MagicMock()),
            MagicMock(),
            MagicMock(),
        )
        mock_server_instance.run.side_effect = KeyboardInterrupt()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", mock_create_app), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", mock_start_chatlog):
            with self.assertRaises(SystemExit):
                run_server(ga)

        ga.web.shutdown.assert_called_once()
        mock_start_chatlog.assert_called_once()  # 启动也被调了（before KeyboardInterrupt）

    def test_keyboard_interrupt_shuts_down_schedule(self):
        """Ctrl+C 时关闭 schedule"""
        ga = MagicMock()
        ga.config = {}
        mock_server_instance = MagicMock()
        mock_server_instance.run.side_effect = KeyboardInterrupt()
        mock_server_cls = MagicMock(return_value=mock_server_instance)
        mock_config_cls = MagicMock()
        mock_create_app = MagicMock(return_value=MagicMock())
        mock_start_chatlog = MagicMock()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", mock_create_app), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", mock_start_chatlog):
            with self.assertRaises(SystemExit):
                run_server(ga)

        ga.schedule.shutdown.assert_called_once()


class TestWSHandlerGetUrl(unittest.TestCase):
    """测试 WSHandler.get_url() — 对应 host/port 配置获取 URL"""

    def test_default_url(self):
        """默认配置下 URL 为 http://localhost:8080/"""
        ga = MagicMock()
        ga.config = {}
        mock_server_cls = MagicMock(return_value=MagicMock())
        mock_config_cls = MagicMock()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", MagicMock(return_value=MagicMock())), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", MagicMock()):
            try:
                run_server(ga, blocking=False)
            except Exception:
                pass
        cfg_kwargs = mock_config_cls.call_args
        host = cfg_kwargs.kwargs.get("host") or cfg_kwargs[0][1]
        port = cfg_kwargs.kwargs.get("port") or cfg_kwargs[0][2]
        url = f"http://{host}:{port}/"
        self.assertEqual(url, "http://localhost:8080/")

    def test_custom_url(self):
        """自定义配置下 URL 正确"""
        ga = MagicMock()
        ga.config = {"server": {"host": "0.0.0.0", "port": 9090}}
        mock_server_cls = MagicMock(return_value=MagicMock())
        mock_config_cls = MagicMock()
        with patch("uvicorn.Server", mock_server_cls), \
             patch("uvicorn.Config", mock_config_cls), \
             patch("chatlens.plugins.web.async_app.create_app", MagicMock(return_value=MagicMock())), \
             patch("chatlens.core._chatlog_runtime.start_chatlog_server", MagicMock()):
            try:
                run_server(ga, blocking=False)
            except Exception:
                pass
        cfg_kwargs = mock_config_cls.call_args
        host = cfg_kwargs.kwargs.get("host") or cfg_kwargs[0][1]
        port = cfg_kwargs.kwargs.get("port") or cfg_kwargs[0][2]
        url = f"http://{host}:{port}/"
        self.assertEqual(url, "http://0.0.0.0:9090/")


if __name__ == '__main__':
    unittest.main()

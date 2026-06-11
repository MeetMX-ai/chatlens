"""iLink Bot 插件单元测试"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from chatlens.plugins.ilink.client import ILinkClient
from chatlens.plugins.ilink.commands import CommandHandler


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Python 3.12 兼容性：确保主线程有 event loop

    不调用 asyncio.get_event_loop()，因为它在 Python 3.12 中已经
    被 DeprecationWarning 标记并将在未来变为 RuntimeError。
    """
    asyncio.set_event_loop(asyncio.new_event_loop())
    yield


class TestILinkClient(unittest.TestCase):
    """测试 iLink 客户端"""

    def test_empty_token_not_connected(self):
        """AC7: 空 token 时 is_connected 返回 False"""
        with patch.object(ILinkClient, "_load_token"):
            client = ILinkClient(token="")
        self.assertFalse(client.is_connected())

    def test_with_token_connected(self):
        """有 token 时 is_connected 返回 True"""
        client = ILinkClient(token="test-token-123")
        self.assertTrue(client.is_connected())

    def test_token_not_in_headers_log(self):
        """AC9: token 不出现在日志中"""
        import logging
        client = ILinkClient(token="secret-token-abc")
        logger = logging.getLogger('chatlens.plugins.ilink')
        messages = []

        class SafeHandler(logging.Handler):
            def emit(self, r):
                messages.append(r.getMessage())

        h = SafeHandler()
        logger.addHandler(h)
        client._headers()
        logger.removeHandler(h)

        for m in messages:
            self.assertNotIn('secret-token-abc', m)
            self.assertNotIn('Bearer', m)

    def test_headers_structure(self):
        """请求头包含必要字段"""
        client = ILinkClient(token="test")
        headers = client._headers()
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(headers['AuthorizationType'], 'ilink_bot_token')
        self.assertIn('Bearer test', headers['Authorization'])
        self.assertIn('X-WECHAT-UIN', headers)

    def test_send_without_context_token_fails(self):
        """无 context_token 时发送失败"""
        client = ILinkClient(token="test")
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            client.send_text("hello", "user@im.wechat")
        )
        self.assertFalse(result)

    def test_load_save_token(self):
        """token 持久化加载"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                'bot_token': 'saved-token',
                'ilink_bot_id': 'bot123',
                'ilink_user_id': 'user456@im.wechat',
                'context_tokens': {'user456@im.wechat': 'ctx_abc'},
            }, f)
            tmp_path = f.name

        try:
            client = ILinkClient(config_path=tmp_path)
            self.assertTrue(client.is_connected())
            self.assertEqual(client.token, 'saved-token')
            self.assertEqual(client.bot_id, 'bot123')
            self.assertEqual(client.user_id, 'user456@im.wechat')
            self.assertEqual(client.context_tokens, {'user456@im.wechat': 'ctx_abc'})
        finally:
            os.unlink(tmp_path)

    def test_context_tokens_thread_safety(self):
        """#7: context_tokens 跨线程读写有锁保护"""
        client = ILinkClient(token="test")
        client.context_tokens = {}
        errors = []

        # asyncio.Lock doesn't support `with` in sync context,
        # so we test that concurrent async access is safe
        async def writer():
            for i in range(100):
                async with client._lock:
                    client.context_tokens[f"user_{i}"] = f"ctx_{i}"

        async def reader():
            for _ in range(100):
                async with client._lock:
                    _ = dict(client.context_tokens)

        async def run():
            await asyncio.gather(writer(), reader())

        import asyncio
        asyncio.get_event_loop().run_until_complete(run())
        self.assertEqual(len(client.context_tokens), 100)


class TestCommandHandler(unittest.TestCase):
    """测试指令处理"""

    def setUp(self):
        self.ga = MagicMock()
        self.ga.config = {}
        self.sent_messages = []
        self.typed_users = []

        def mock_send(text, user_id):
            self.sent_messages.append((user_id, text))
            return True

        def mock_typing(user_id):
            self.typed_users.append(user_id)

        self.handler = CommandHandler(
            ga=self.ga,
            send_func=mock_send,
            typing_func=mock_typing,
        )

    def _last_reply(self):
        """获取最后一条回复"""
        if not self.sent_messages:
            return ""
        return self.sent_messages[-1][1]

    def test_help_command(self):
        """AC1: /帮助 返回指令列表"""
        self.handler.handle("/帮助", "user1")
        reply = self._last_reply()
        self.assertIn("可用指令", reply)
        self.assertIn("/群列表", reply)
        self.assertIn("/统计", reply)
        self.assertIn("/分析", reply)
        self.assertIn("/日报", reply)

    def test_groups_command_empty(self):
        """AC2: 无群聊时返回提示"""
        self.ga.get_groups.return_value = []
        self.handler.handle("/群列表", "user1")
        reply = self._last_reply()
        self.assertIn("暂无", reply)

    def test_groups_command_with_data(self):
        """AC2: 有群聊时返回列表"""
        self.ga.get_groups.return_value = ['group1@chatroom', 'group2@chatroom']
        self.ga.providers.get_available.return_value = []
        self.handler.handle("/群列表", "user1")
        reply = self._last_reply()
        self.assertIn("2 个群聊", reply)
        self.assertIn("group1@chatroom", reply)

    def test_stats_command_no_data(self):
        """AC5: 不存在的群名返回友好提示"""
        self.ga.get_messages.return_value = []
        self.handler.handle("/统计 不存在的群", "user1")
        reply = self._last_reply()
        self.assertIn("未找到", reply)

    def test_stats_command_with_real_structure(self):
        """AC3 + #1: 使用实际 overview 数据结构"""
        from chatlens.core.models import ChatMessage
        msgs = [ChatMessage(sender="Alice", content="hi", msg_type="text",
                            msg_attr="friend", timestamp="2026-06-01 10:00:00",
                            group_name="test")]
        self.ga.get_messages.return_value = msgs
        # 使用实际数据结构: time_range.start/end, avg_messages_per_day
        self.ga.stats_analyzer.analyze.return_value = {
            'overview': {
                'total_messages': 100,
                'total_members': 10,
                'time_range': {
                    'start': '2026-01-01 00:00:00',
                    'end': '2026-06-01 23:59:59',
                },
                'avg_messages_per_day': 5,
            },
            'member_stats': [
                {'sender': 'Alice', 'message_count': 30},
                {'sender': 'Bob', 'message_count': 20},
            ],
        }
        self.handler.handle("/统计 测试群", "user1")
        reply = self._last_reply()
        self.assertIn("100", reply)
        self.assertIn("10", reply)
        self.assertIn("2026-01-01", reply)
        self.assertIn("2026-06-01", reply)
        self.assertIn("Alice", reply)

    def test_analyze_user_titles_uses_name_field(self):
        """#2: user_titles 使用 name 字段而非 user"""
        self.ga.get_messages.return_value = [MagicMock()]
        self.ga.web = MagicMock()
        self.ga.web.auto_analyze.return_value = {
            'success': True,
            'method': 'rule',
            'data': {
                'summary': {'summary': '测试摘要'},
                'user_titles': {
                    'user_titles': [
                        {'name': '张三', 'title': '话痨王', 'mbti': 'ENFP'},
                    ]
                },
                'golden_quotes': {
                    'golden_quotes': [
                        {'content': '金句内容', 'sender': '张三'},
                    ]
                },
                'chat_quality': {
                    'title': '质量标题',
                    'subtitle': '质量副标题',
                    'summary': '质量总结',
                },
            },
            'report': {},
        }
        self.handler.handle("/分析 测试群", "user1")
        reply = self._last_reply()
        self.assertIn("张三", reply)
        self.assertIn("话痨王", reply)

    def test_analyze_chat_quality_uses_summary(self):
        """#3: chat_quality 使用 summary/subtitle 而非 comment"""
        self.ga.get_messages.return_value = [MagicMock()]
        self.ga.web = MagicMock()
        self.ga.web.auto_analyze.return_value = {
            'success': True,
            'method': 'rule',
            'data': {
                'summary': {'summary': ''},
                'user_titles': {'user_titles': []},
                'golden_quotes': {'golden_quotes': []},
                'chat_quality': {
                    'title': '质量标题',
                    'subtitle': '质量副标题',
                    'summary': '质量总结内容',
                },
            },
            'report': {},
        }
        self.handler.handle("/分析 测试群", "user1")
        reply = self._last_reply()
        self.assertIn("质量总结内容", reply)

    def test_analyze_falls_back_to_subtitle(self):
        """#3: chat_quality summary 为空时回退到 subtitle"""
        self.ga.get_messages.return_value = [MagicMock()]
        self.ga.web = MagicMock()
        self.ga.web.auto_analyze.return_value = {
            'success': True,
            'method': 'rule',
            'data': {
                'summary': {'summary': ''},
                'user_titles': {'user_titles': []},
                'golden_quotes': {'golden_quotes': []},
                'chat_quality': {
                    'title': '质量标题',
                    'subtitle': '副标题回退',
                    'summary': '',
                },
            },
            'report': {},
        }
        self.handler.handle("/分析 测试群", "user1")
        reply = self._last_reply()
        self.assertIn("副标题回退", reply)

    def test_unknown_command(self):
        """AC6: 未知指令返回提示"""
        self.handler.handle("/随便什么", "user1")
        reply = self._last_reply()
        self.assertIn("未知指令", reply)

    def test_no_prefix_ignored(self):
        """非 / 开头的消息被忽略"""
        self.handler.handle("你好", "user1")
        self.assertEqual(len(self.sent_messages), 0)

    def test_analyze_command_no_data(self):
        """AC5: 分析不存在的群名"""
        self.ga.get_messages.return_value = []
        self.handler.handle("/分析 不存在的群", "user1")
        reply = self._last_reply()
        self.assertIn("未找到", reply)

    def test_daily_command_missing_args(self):
        """日报指令参数不足"""
        self.handler.handle("/日报 测试群", "user1")
        reply = self._last_reply()
        self.assertIn("用法", reply)

    def test_schedule_command_missing_args(self):
        """定时指令参数不足"""
        self.handler.handle("/定时 测试群", "user1")
        reply = self._last_reply()
        self.assertIn("用法", reply)

    def test_status_command(self):
        """状态指令"""
        self.ga.get_groups.return_value = ['g1']
        self.ga.has_api_key.return_value = True
        wechat = MagicMock()
        wechat.is_available.return_value = True
        self.ga.get_provider.return_value = wechat
        self.handler.handle("/状态", "user1")
        reply = self._last_reply()
        self.assertIn("系统状态", reply)
        self.assertIn("1", reply)
        self.assertIn("已配置", reply)

    def test_server_url_from_config(self):
        """#8: 服务器地址从配置读取，不硬编码"""
        self.ga.config = {'server': {'host': '0.0.0.0', 'port': 9090}}
        url = self.handler._get_server_url()
        self.assertEqual(url, "http://0.0.0.0:9090")

    def test_server_url_default(self):
        """#8: 默认服务器地址"""
        self.ga.config = {}
        url = self.handler._get_server_url()
        self.assertEqual(url, "http://localhost:8080")


class TestILinkPlugin(unittest.TestCase):
    """测试 ILinkPlugin 类"""

    def test_setup_register_creates_service(self):
        """ILinkPlugin.setup() — 插件初始化时创建 ILinkService 并挂载到 ga"""
        from chatlens.plugins.ilink import ILinkPlugin
        ga = MagicMock()
        ga.config = {"ilink": {"enabled": False}}
        plugin = ILinkPlugin()
        plugin.register(ga)
        self.assertTrue(hasattr(ga, 'ilink'))
        self.assertIsNotNone(ga.ilink)

    @patch("chatlens.plugins.ilink.client.ILinkClient")
    @patch("chatlens.plugins.ilink.commands.CommandHandler")
    def test_start_calls_client_start_polling(self, MockHandler, MockClient):
        """ILinkPlugin.start() — 启动时调用 ILinkClient.start（异步）"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {"enabled": True, "bot_token": "test-token"}}
        service = ILinkService(ga)

        # 使用 AsyncMock 以便 asyncio.run_until_complete 能正常处理
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        # start() 返回一个 coroutine（AsyncMock 自动处理）
        import unittest.mock as _um
        mock_client.start = _um.AsyncMock()
        mock_client.stop = _um.AsyncMock()
        MockClient.return_value = mock_client

        service.start()
        # 等待轮询线程启动完成
        import time as _t
        for _ in range(50):
            if hasattr(service, "_poll_thread") and service._poll_thread.is_alive():
                break
            _t.sleep(0.05)
        # 给 run_until_complete 一点时间执行 start
        _t.sleep(0.1)
        mock_client.start.assert_called_once()
        self.assertTrue(service._started)
        # 清理线程
        service.shutdown()

    @patch("chatlens.plugins.ilink.client.ILinkClient")
    @patch("chatlens.plugins.ilink.commands.CommandHandler")
    def test_start_disabled_does_not_start(self, MockHandler, MockClient):
        """ILinkPlugin.start() — 插件禁用时不启动"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {"enabled": False}}
        service = ILinkService(ga)
        service.start()
        self.assertFalse(service._started)
        MockClient.assert_not_called()

    @patch("chatlens.plugins.ilink.client.ILinkClient")
    @patch("chatlens.plugins.ilink.commands.CommandHandler")
    def test_start_not_connected_does_not_poll(self, MockHandler, MockClient):
        """ILinkPlugin.start() — 客户端未连接时不开始轮询"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {"enabled": True, "bot_token": "bad-token"}}
        service = ILinkService(ga)

        mock_client = MagicMock()
        mock_client.is_connected.return_value = False
        MockClient.return_value = mock_client

        service.start()
        # start() 不应被调用
        if hasattr(mock_client, "start"):
            mock_client.start.assert_not_called()
        self.assertFalse(service._started)

    def test_stop_sets_started_false(self):
        """ILinkPlugin.stop() — 停止时设置 _started 为 False"""
        from chatlens.plugins.ilink import ILinkService
        import unittest.mock as _um
        ga = MagicMock()
        ga.config = {"ilink": {"enabled": True}}
        service = ILinkService(ga)
        service._started = True
        mock_client = MagicMock()
        mock_client.stop = _um.AsyncMock()
        service.client = mock_client
        service.shutdown()
        self.assertFalse(service._started)
        mock_client.stop.assert_called_once()

    def test_stop_no_client_no_error(self):
        """ILinkPlugin.stop() — 无客户端时停止不报错"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {}}
        service = ILinkService(ga)
        service._started = True
        service.shutdown()
        self.assertFalse(service._started)

    def test_on_message_handles_command(self):
        """ILinkPlugin.on_message() — 处理 / 开头的消息"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {}}
        service = ILinkService(ga)
        mock_handler = MagicMock()
        service.handler = mock_handler

        msg = {
            "from_user_id": "user1",
            "item_list": [{"type": 1, "text_item": {"text": "/帮助"}}],
        }
        service._on_message(msg)
        mock_handler.handle.assert_called_once_with("/帮助", "user1")

    def test_on_message_ignores_non_command(self):
        """ILinkPlugin.on_message() — 非 / 开头的消息被忽略"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {}}
        service = ILinkService(ga)
        mock_handler = MagicMock()
        service.handler = mock_handler

        msg = {
            "from_user_id": "user1",
            "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
        }
        service._on_message(msg)
        mock_handler.handle.assert_not_called()

    def test_on_message_ignores_empty_text(self):
        """ILinkPlugin.on_message() — 空文本消息被忽略"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {}}
        service = ILinkService(ga)
        mock_handler = MagicMock()
        service.handler = mock_handler

        msg = {
            "from_user_id": "user1",
            "item_list": [{"type": 2}],
        }
        service._on_message(msg)
        mock_handler.handle.assert_not_called()

    def test_on_message_ignores_no_user(self):
        """ILinkPlugin.on_message() — 无 from_user_id 的消息被忽略"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {}}
        service = ILinkService(ga)
        mock_handler = MagicMock()
        service.handler = mock_handler

        msg = {
            "item_list": [{"type": 1, "text_item": {"text": "/帮助"}}],
        }
        service._on_message(msg)
        mock_handler.handle.assert_not_called()

    def test_get_status_connected(self):
        """ILinkPlugin.get_status() — 已连接时返回 True"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {}}
        service = ILinkService(ga)
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        service.client = mock_client
        self.assertTrue(service.is_connected())

    def test_get_status_disconnected(self):
        """ILinkPlugin.get_status() — 未连接时返回 False"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {}}
        service = ILinkService(ga)
        self.assertFalse(service.is_connected())

    def test_get_status_no_client(self):
        """ILinkPlugin.get_status() — 无客户端时返回 False"""
        from chatlens.plugins.ilink import ILinkService
        ga = MagicMock()
        ga.config = {"ilink": {}}
        service = ILinkService(ga)
        service.client = None
        self.assertFalse(service.is_connected())


class TestPluginRegistration(unittest.TestCase):
    """测试插件注册"""

    def test_plugin_discovery(self):
        """AC10: ilink 插件被自动发现"""
        from chatlens.core import PluginRegistry
        r = PluginRegistry()
        r.discover()
        names = [p.name for p in r.plugins]
        self.assertIn('ilink', names)

    def test_plugin_register(self):
        """AC10: 插件注册后 ga 上挂载 ilink 属性"""
        from chatlens.core import GroupAnalysis, PluginRegistry
        ga = GroupAnalysis()
        r = PluginRegistry()
        r.discover()
        r.load_all(ga)
        self.assertTrue(hasattr(ga, 'ilink'))
        self.assertIsNotNone(ga.ilink)


if __name__ == '__main__':
    unittest.main()

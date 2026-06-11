"""ilink/commands.py — CommandHandler 单元测试（补充覆盖）"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatlens.plugins.ilink.commands import CommandHandler


class TestCommandHandlerInit(unittest.TestCase):
    """测试 CommandHandler.__init__()"""

    def test_init_stores_ga(self):
        ga = MagicMock()
        handler = CommandHandler(ga=ga, send_func=lambda t, u: True, typing_func=lambda u: None)
        self.assertIs(handler.ga, ga)

    def test_init_stores_send_func(self):
        sent = []
        handler = CommandHandler(
            ga=MagicMock(),
            send_func=lambda t, u: sent.append(t),
            typing_func=lambda u: None,
        )
        handler.send("hello", "user1")
        self.assertEqual(sent, ["hello"])

    def test_init_stores_typing_func(self):
        typed = []
        handler = CommandHandler(
            ga=MagicMock(),
            send_func=lambda t, u: True,
            typing_func=lambda u: typed.append(u),
        )
        handler.typing("user1")
        self.assertEqual(typed, ["user1"])

    def test_init_registers_all_commands(self):
        handler = CommandHandler(ga=MagicMock(), send_func=lambda t, u: True, typing_func=lambda u: None)
        expected = {"帮助", "群列表", "统计", "分析", "日报", "定时", "状态"}
        self.assertEqual(set(handler._commands.keys()), expected)

    def test_prefix_is_slash(self):
        self.assertEqual(CommandHandler.PREFIX, "/")


class TestCommandHandlerLogin(unittest.TestCase):
    """测试 /状态 中涉及 iLink 连接状态的逻辑（对应 cmd_login 场景）"""

    def setUp(self):
        self.ga = MagicMock()
        self.sent = []
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=lambda t, u: self.sent.append((u, t)) or True,
            typing_func=lambda u: None,
        )

    def test_status_ilink_connected(self):
        """iLink 已连接时状态显示'已连接'"""
        self.ga.get_groups.return_value = ["g1"]
        self.ga.has_api_key.return_value = False
        wechat = MagicMock()
        wechat.is_available.return_value = False
        self.ga.get_provider.return_value = wechat
        self.ga.ilink = MagicMock()
        self.ga.ilink.is_connected.return_value = True
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("已连接", reply)

    def test_status_ilink_not_connected(self):
        """iLink 未连接时状态显示'未连接'"""
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        self.ga.get_provider.return_value = None
        # MagicMock 自动创建属性，需显式设置 ilink.is_connected = False
        self.ga.ilink = MagicMock()
        self.ga.ilink.is_connected.return_value = False
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("未连接", reply)


class TestCommandHandlerStatus(unittest.TestCase):
    """测试 /状态 指令"""

    def setUp(self):
        self.ga = MagicMock()
        self.sent = []
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=lambda t, u: self.sent.append((u, t)) or True,
            typing_func=lambda u: None,
        )

    def test_status_shows_group_count(self):
        self.ga.get_groups.return_value = ["g1", "g2", "g3"]
        self.ga.has_api_key.return_value = True
        wechat = MagicMock()
        wechat.is_available.return_value = True
        self.ga.get_provider.return_value = wechat
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("3", reply)

    def test_status_api_configured(self):
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = True
        self.ga.get_provider.return_value = None
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("已配置", reply)

    def test_status_api_not_configured(self):
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        self.ga.get_provider.return_value = None
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("未配置", reply)

    def test_status_wechat_available(self):
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        wechat = MagicMock()
        wechat.is_available.return_value = True
        self.ga.get_provider.return_value = wechat
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("可用", reply)

    def test_status_wechat_unavailable(self):
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        wechat = MagicMock()
        wechat.is_available.return_value = False
        self.ga.get_provider.return_value = wechat
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("不可用", reply)

    def test_status_with_schedule(self):
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        self.ga.get_provider.return_value = None
        self.ga.schedule = MagicMock()
        self.ga.schedule.list_all.return_value = {"tasks": [{"id": "1"}, {"id": "2"}]}
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("2 个", reply)

    def test_status_no_schedule(self):
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        self.ga.get_provider.return_value = None
        # MagicMock 自动创建属性，需显式删除 schedule
        del self.ga.schedule
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertNotIn("定时任务", reply)


class TestCommandHandlerSend(unittest.TestCase):
    """测试消息发送相关逻辑（通过 /统计 和 /分析 验证 send 调用）"""

    def setUp(self):
        self.ga = MagicMock()
        self.sent = []
        self.typed = []
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=lambda t, u: self.sent.append((u, t)) or True,
            typing_func=lambda u: self.typed.append(u),
        )

    def test_stats_sends_reply_to_correct_user(self):
        from chatlens.core.models import ChatMessage
        msgs = [ChatMessage(sender="A", content="hi", msg_type="text",
                            msg_attr="friend", timestamp="2026-06-01 10:00:00",
                            group_name="g")]
        self.ga.get_messages.return_value = msgs
        self.ga.stats_analyzer.analyze.return_value = {
            "overview": {"total_messages": 1, "total_members": 1,
                         "time_range": {"start": "2026-06-01", "end": "2026-06-01"},
                         "avg_messages_per_day": 1},
            "member_stats": [],
        }
        self.handler.handle("/统计 g", "target_user")
        self.assertEqual(self.sent[-1][0], "target_user")

    def test_analyze_sends_progress_then_result(self):
        from chatlens.core.models import ChatMessage
        msgs = [ChatMessage(sender="A", content="hi", msg_type="text",
                            msg_attr="friend", timestamp="2026-06-01 10:00:00",
                            group_name="g")]
        self.ga.get_messages.return_value = msgs
        self.ga.web = MagicMock()
        self.ga.web.auto_analyze.return_value = {
            "success": True, "method": "rule", "data": {
                "summary": {"summary": "s"},
                "user_titles": {"user_titles": []},
                "golden_quotes": {"golden_quotes": []},
                "chat_quality": {},
            }, "report": {},
        }
        self.handler.handle("/分析 g", "user1")
        # 应至少发送两条消息: 进度提示 + 结果
        texts = [s[1] for s in self.sent]
        self.assertTrue(any("正在分析" in t for t in texts))
        self.assertTrue(any("分析结果" in t for t in texts))

    def test_analyze_no_web_sends_error(self):
        from chatlens.core.models import ChatMessage
        msgs = [ChatMessage(sender="A", content="hi", msg_type="text",
                            msg_attr="friend", timestamp="2026-06-01 10:00:00",
                            group_name="g")]
        self.ga.get_messages.return_value = msgs
        self.ga.web = None
        self.handler.handle("/分析 g", "user1")
        reply = self.sent[-1][1]
        self.assertIn("Web 服务未启用", reply)

    def test_analyze_failure_sends_error(self):
        from chatlens.core.models import ChatMessage
        msgs = [ChatMessage(sender="A", content="hi", msg_type="text",
                            msg_attr="friend", timestamp="2026-06-01 10:00:00",
                            group_name="g")]
        self.ga.get_messages.return_value = msgs
        self.ga.web = MagicMock()
        self.ga.web.auto_analyze.return_value = {
            "success": False, "error": "API 超时",
        }
        self.handler.handle("/分析 g", "user1")
        reply = self.sent[-1][1]
        self.assertIn("API 超时", reply)


class TestCommandHandlerContacts(unittest.TestCase):
    """测试 /群列表 指令（对应联系人列表场景）"""

    def setUp(self):
        self.ga = MagicMock()
        self.sent = []
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=lambda t, u: self.sent.append((u, t)) or True,
            typing_func=lambda u: None,
        )

    def test_groups_with_display_name(self):
        """群列表使用 provider 的 get_display_name"""
        self.ga.get_groups.return_value = ["room1@chatroom"]
        provider = MagicMock()
        provider.get_display_name.return_value = "技术交流群"
        self.ga.providers.get_available.return_value = [provider]
        self.handler.handle("/群列表", "user1")
        reply = self.sent[-1][1]
        self.assertIn("技术交流群", reply)

    def test_groups_display_name_fallback_on_error(self):
        """provider.get_display_name 抛异常时回退到原始名"""
        self.ga.get_groups.return_value = ["room1@chatroom"]
        provider = MagicMock()
        provider.get_display_name.side_effect = Exception("db error")
        self.ga.providers.get_available.return_value = [provider]
        self.handler.handle("/群列表", "user1")
        reply = self.sent[-1][1]
        self.assertIn("room1@chatroom", reply)

    def test_groups_truncates_at_20(self):
        """超过 20 个群聊时截断显示"""
        groups = [f"group_{i}" for i in range(25)]
        self.ga.get_groups.return_value = groups
        self.ga.providers.get_available.return_value = []
        self.handler.handle("/群列表", "user1")
        reply = self.sent[-1][1]
        self.assertIn("还有 5 个", reply)


class TestCommandHandlerHistory(unittest.TestCase):
    """测试 /日报 指令（对应历史消息场景）"""

    def setUp(self):
        self.ga = MagicMock()
        self.sent = []
        self.typed = []
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=lambda t, u: self.sent.append((u, t)) or True,
            typing_func=lambda u: self.typed.append(u),
        )

    def test_daily_success(self):
        from chatlens.core.models import ChatMessage
        msgs = [ChatMessage(sender="A", content="hi", msg_type="text",
                            msg_attr="friend", timestamp="2026-06-01 10:00:00",
                            group_name="g")]
        self.ga.get_messages.return_value = msgs
        self.ga.web = MagicMock()
        self.ga.web.daily_auto_report.return_value = {
            "success": True,
            "message_count": 42,
            "report": {"image_url": "/reports/daily.png"},
        }
        self.handler.handle("/日报 测试群 2026-06-01", "user1")
        reply = self.sent[-1][1]
        self.assertIn("42", reply)
        self.assertIn("2026-06-01", reply)

    def test_daily_no_data(self):
        self.ga.get_messages.return_value = []
        self.handler.handle("/日报 测试群 2026-06-01", "user1")
        reply = self.sent[-1][1]
        self.assertIn("未找到", reply)

    def test_daily_no_web(self):
        from chatlens.core.models import ChatMessage
        msgs = [ChatMessage(sender="A", content="hi", msg_type="text",
                            msg_attr="friend", timestamp="2026-06-01 10:00:00",
                            group_name="g")]
        self.ga.get_messages.return_value = msgs
        self.ga.web = None
        self.handler.handle("/日报 测试群 2026-06-01", "user1")
        reply = self.sent[-1][1]
        self.assertIn("Web 服务未启用", reply)

    def test_daily_failure(self):
        from chatlens.core.models import ChatMessage
        msgs = [ChatMessage(sender="A", content="hi", msg_type="text",
                            msg_attr="friend", timestamp="2026-06-01 10:00:00",
                            group_name="g")]
        self.ga.get_messages.return_value = msgs
        self.ga.web = MagicMock()
        self.ga.web.daily_auto_report.return_value = {
            "success": False, "error": "生成失败",
        }
        self.handler.handle("/日报 测试群 2026-06-01", "user1")
        reply = self.sent[-1][1]
        self.assertIn("生成失败", reply)


class TestCommandHandlerPoll(unittest.TestCase):
    """测试消息轮询相关逻辑（通过 handle 方法验证消息分发）"""

    def setUp(self):
        self.ga = MagicMock()
        self.sent = []
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=lambda t, u: self.sent.append((u, t)) or True,
            typing_func=lambda u: None,
        )

    def test_handle_strips_whitespace(self):
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        self.ga.get_provider.return_value = None
        self.handler.handle("  /状态  ", "user1")
        self.assertTrue(len(self.sent) > 0)

    def test_handle_empty_after_prefix(self):
        """/ 后无内容时返回未知指令"""
        self.handler.handle("/", "user1")
        reply = self.sent[-1][1]
        self.assertIn("未知指令", reply)

    def test_handle_exception_in_handler(self):
        """指令处理异常时发送错误消息"""
        self.ga.get_messages.side_effect = RuntimeError("boom")
        self.handler.handle("/统计 测试群", "user1")
        reply = self.sent[-1][1]
        self.assertIn("出错", reply)

    def test_handle_multiple_commands_in_sequence(self):
        """连续处理多条指令"""
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        self.ga.get_provider.return_value = None
        self.handler.handle("/帮助", "user1")
        self.handler.handle("/状态", "user1")
        self.assertEqual(len(self.sent), 2)


class TestCommandHandlerLogout(unittest.TestCase):
    """测试登出相关逻辑（通过 /状态 验证 iLink 断连状态）"""

    def setUp(self):
        self.ga = MagicMock()
        self.sent = []
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=lambda t, u: self.sent.append((u, t)) or True,
            typing_func=lambda u: None,
        )

    def test_status_after_ilink_disconnect(self):
        """iLink 断开后状态显示'未连接'"""
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        self.ga.get_provider.return_value = None
        # 模拟 ilink 存在但未连接
        self.ga.ilink = MagicMock()
        self.ga.ilink.is_connected.return_value = False
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("未连接", reply)

    def test_status_no_ilink_attribute(self):
        """无 ilink 属性时状态显示'未连接'"""
        self.ga.get_groups.return_value = []
        self.ga.has_api_key.return_value = False
        self.ga.get_provider.return_value = None
        # MagicMock 自动创建属性，需显式删除 ilink
        del self.ga.ilink
        self.handler.handle("/状态", "user1")
        reply = self.sent[-1][1]
        self.assertIn("未连接", reply)


class TestCommandHandlerSchedule(unittest.TestCase):
    """测试 /定时 指令"""

    def setUp(self):
        self.ga = MagicMock()
        self.sent = []
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=lambda t, u: self.sent.append((u, t)) or True,
            typing_func=lambda u: None,
        )

    def test_schedule_success(self):
        self.ga.schedule = MagicMock()
        self.ga.schedule.create.return_value = {"success": True}
        self.handler.handle("/定时 测试群 09:00", "user1")
        reply = self.sent[-1][1]
        self.assertIn("已创建", reply)
        self.assertIn("09:00", reply)

    def test_schedule_failure(self):
        self.ga.schedule = MagicMock()
        self.ga.schedule.create.return_value = {"success": False, "error": "已存在"}
        self.handler.handle("/定时 测试群 09:00", "user1")
        reply = self.sent[-1][1]
        self.assertIn("创建失败", reply)

    def test_schedule_invalid_time(self):
        """时间格式错误时提示"""
        self.handler.handle("/定时 测试群 abc", "user1")
        reply = self.sent[-1][1]
        self.assertIn("时间格式错误", reply)

    def test_schedule_no_plugin(self):
        self.ga.schedule = None
        self.handler.handle("/定时 测试群 09:00", "user1")
        reply = self.sent[-1][1]
        self.assertIn("定时任务插件未启用", reply)


if __name__ == "__main__":
    unittest.main()

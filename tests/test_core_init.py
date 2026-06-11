"""core/__init__.py — GroupAnalysis 单元测试（补充覆盖）"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatlens.core import GroupAnalysis
from chatlens.core.models import ChatMessage


def _make_messages(n=5):
    return [
        ChatMessage(
            sender=f"用户{i % 3}",
            content=f"消息内容{i}",
            msg_type="text",
            msg_attr="",
            timestamp=f"2026-06-{(i % 28) + 1:02d} 10:00:00",
            group_name="测试群",
        )
        for i in range(n)
    ]


class TestGroupAnalysisInit(unittest.TestCase):
    """测试 GroupAnalysis.__init__()"""

    def test_init_default_config(self):
        """无参数时使用空配置"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertEqual(ga.config, {"data_dir": tmpdir})

    def test_init_custom_config(self):
        """自定义配置被保留"""
        config = {"data_dir": tempfile.mkdtemp(), "ai_service": {"api_key": "test"}}
        ga = GroupAnalysis(config)
        self.assertEqual(ga.config["ai_service"]["api_key"], "test")

    def test_init_creates_data_dir(self):
        """自动创建 data_dir"""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "new_subdir")
            ga = GroupAnalysis({"data_dir": data_dir})
            self.assertTrue(os.path.exists(data_dir))

    def test_init_collector_data_empty(self):
        """初始 collector_data 为空"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertEqual(ga.collector_data, {})

    def test_init_stats_analyzer(self):
        """初始化时创建 stats_analyzer"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertIsNotNone(ga.stats_analyzer)

    def test_init_ai_analyzer(self):
        """初始化时创建 ai_analyzer"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertIsNotNone(ga.ai_analyzer)

    def test_init_providers_registry(self):
        """初始化时创建 providers 注册表"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertIsNotNone(ga.providers)

    def test_init_default_wechat_provider(self):
        """无 providers 参数时默认注册 wechat provider"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            wechat = ga.providers.get("wechat")
            self.assertIsNotNone(wechat)

    def test_init_custom_providers(self):
        """传入 providers 参数时使用自定义列表"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_provider = MagicMock()
            mock_provider.name = "custom"
            ga = GroupAnalysis({"data_dir": tmpdir}, providers=[mock_provider])
            self.assertIsNotNone(ga.providers.get("custom"))
            self.assertIsNone(ga.providers.get("wechat"))


class TestGroupAnalysisGetProvider(unittest.TestCase):
    """测试 GroupAnalysis.get_provider()"""

    def test_get_wechat_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            provider = ga.get_provider("wechat")
            self.assertIsNotNone(provider)
            self.assertEqual(provider.name, "wechat")

    def test_get_nonexistent_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            provider = ga.get_provider("telegram")
            self.assertIsNone(provider)

    def test_get_provider_default_is_wechat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            provider = ga.get_provider()
            self.assertIsNotNone(provider)


class TestGroupAnalysisGetDisplayName(unittest.TestCase):
    """测试通过 provider 获取显示名（对应 get_display_name 场景）"""

    def test_provider_get_display_name(self):
        """provider.get_display_name 返回显示名"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            provider = ga.get_provider("wechat")
            # mock bridge 的 _get_display_name
            provider._bridge._get_display_name = MagicMock(return_value="技术交流群")
            result = provider.get_display_name("room1@chatroom")
            self.assertEqual(result, "技术交流群")

    def test_provider_get_display_name_fallback(self):
        """provider.get_display_name 回退到原始名"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            provider = ga.get_provider("wechat")
            provider._bridge._get_display_name = MagicMock(return_value="room1@chatroom")
            result = provider.get_display_name("room1@chatroom")
            self.assertEqual(result, "room1@chatroom")


class TestGroupAnalysisLoadFromChatlog(unittest.TestCase):
    """测试 GroupAnalysis.load_from_chatlog()"""

    def test_load_from_chatlog_success(self):
        """成功从 chatlog 加载消息"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            msgs = _make_messages(3)
            # mock chatlog 的 get_messages
            ga.chatlog.get_messages = MagicMock(return_value=msgs)
            result = ga.load_from_chatlog("test_room")
            self.assertEqual(len(result), 3)
            self.assertIn("test_room", ga.collector_data)

    def test_load_from_chatlog_empty(self):
        """chatlog 返回空列表"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            ga.chatlog.get_messages = MagicMock(return_value=[])
            result = ga.load_from_chatlog("test_room")
            self.assertEqual(result, [])
            self.assertNotIn("test_room", ga.collector_data)

    def test_load_from_chatlog_none(self):
        """chatlog 返回 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            ga.chatlog.get_messages = MagicMock(return_value=None)
            result = ga.load_from_chatlog("test_room")
            self.assertEqual(result, [])

    def test_load_from_chatlog_with_limit(self):
        """带 limit 参数加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            msgs = _make_messages(5)
            ga.chatlog.get_messages = MagicMock(return_value=msgs[:2])
            result = ga.load_from_chatlog("test_room", limit=2)
            ga.chatlog.get_messages.assert_called_once_with("test_room", 2)

    def test_load_from_chatlog_no_bridge(self):
        """无 chatlog bridge 时返回空列表"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            # 移除 wechat provider 使 chatlog 为 None
            ga.providers._providers.pop("wechat", None)
            self.assertIsNone(ga.chatlog)


class TestGroupAnalysisGetStats(unittest.TestCase):
    """测试 GroupAnalysis 统计相关方法（对应 get_stats 场景）"""

    def test_analyze_returns_stats(self):
        """analyze() 返回统计结果"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            msgs = _make_messages(10)
            result = ga.analyze(msgs)
            self.assertIn("overview", result)
            self.assertEqual(result["overview"]["total_messages"], 10)

    def test_get_stats_analyzer(self):
        """get_stats_analyzer() 返回分析器"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            analyzer = ga.get_stats_analyzer()
            self.assertIsNotNone(analyzer)

    def test_ai_analyze_with_rules(self):
        """ai_analyze(use_rules=True) 使用规则分析"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            msgs = _make_messages(5)
            result = ga.ai_analyze(msgs, use_rules=True)
            self.assertIn("summary", result)

    def test_has_api_key_false(self):
        """未配置 API Key 时返回 False"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertFalse(ga.has_api_key())

    def test_has_api_key_placeholder(self):
        """占位符 API Key 时返回 False"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir, "ai_service": {"api_key": "YOUR_API_KEY_HERE"}})
            self.assertFalse(ga.has_api_key())

    def test_is_api_key_placeholder_true(self):
        """占位符 API Key 检测"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir, "ai_service": {"api_key": "PLACEHOLDER"}})
            self.assertTrue(ga.is_api_key_placeholder())

    def test_is_api_key_placeholder_empty(self):
        """空 API Key 视为占位符"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertTrue(ga.is_api_key_placeholder())


class TestGroupAnalysisCacheInvalidation(unittest.TestCase):
    """测试缓存失效相关逻辑（对应 invalidate_cache 场景）"""

    def test_delete_loaded_clears_cache(self):
        """删除已加载数据后 collector_data 被清空"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            msgs = _make_messages(3)
            ga.set_messages("test_group", msgs)
            self.assertIn("test_group", ga.collector_data)
            ga.delete_loaded("test_group")
            self.assertNotIn("test_group", ga.collector_data)

    def test_delete_loaded_removes_file(self):
        """删除已加载数据后文件也被删除"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            msgs = _make_messages(2)
            ga.save_loaded("test_group", msgs)
            filepath = ga.get_data_file_path("test_group")
            self.assertTrue(os.path.exists(filepath))
            ga.delete_loaded("test_group")
            self.assertFalse(os.path.exists(filepath))

    def test_set_messages_overwrites(self):
        """set_messages 覆盖已有数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            ga.set_messages("g1", _make_messages(3))
            ga.set_messages("g1", _make_messages(7))
            self.assertEqual(len(ga.get_messages("g1")), 7)

    def test_get_messages_from_file_after_cache_clear(self):
        """清除内存缓存后可从文件重新加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            msgs = _make_messages(3)
            ga.save_loaded("test_group", msgs)
            ga.collector_data.pop("test_group", None)
            loaded = ga.get_messages("test_group")
            self.assertEqual(len(loaded), 3)


class TestGroupAnalysisLoadFromProvider(unittest.TestCase):
    """测试 GroupAnalysis.load_from_provider()"""

    def test_load_from_provider_success(self):
        """从 provider 加载消息成功"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            msgs = _make_messages(4)
            provider = ga.get_provider("wechat")
            provider.get_messages = MagicMock(return_value=msgs)
            result = ga.load_from_provider("test_room", "wechat")
            self.assertEqual(len(result), 4)
            self.assertIn("test_room", ga.collector_data)

    def test_load_from_provider_not_found(self):
        """provider 不存在时返回空列表"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            result = ga.load_from_provider("test_room", "nonexistent")
            self.assertEqual(result, [])

    def test_load_from_provider_empty_result(self):
        """provider 返回空列表时不写入 collector_data"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            provider = ga.get_provider("wechat")
            provider.get_messages = MagicMock(return_value=[])
            result = ga.load_from_provider("test_room", "wechat")
            self.assertEqual(result, [])
            self.assertNotIn("test_room", ga.collector_data)


class TestGroupAnalysisMisc(unittest.TestCase):
    """测试其他 GroupAnalysis 方法"""

    def test_chatlog_property(self):
        """chatlog 属性返回 bridge"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertIsNotNone(ga.chatlog)

    def test_chatlog_property_no_provider(self):
        """无 wechat provider 时 chatlog 为 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            ga.providers._providers.pop("wechat", None)
            self.assertIsNone(ga.chatlog)

    def test_has_messages_in_memory(self):
        """内存中有消息时 has_messages 返回 True"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            ga.set_messages("g1", _make_messages(1))
            self.assertTrue(ga.has_messages("g1"))

    def test_has_messages_on_disk(self):
        """磁盘上有文件时 has_messages 返回 True"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            ga.save_loaded("g1", _make_messages(1))
            ga.collector_data.pop("g1", None)
            self.assertTrue(ga.has_messages("g1"))

    def test_has_messages_not_found(self):
        """无消息时 has_messages 返回 False"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertFalse(ga.has_messages("nonexistent"))

    def test_get_config_returns_copy(self):
        """get_config 返回配置的副本"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir, "key": "val"})
            cfg = ga.get_config()
            cfg["key"] = "modified"
            self.assertEqual(ga.config["key"], "val")

    def test_get_loaded_count(self):
        """get_loaded_count 返回消息数量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            ga.set_messages("g1", _make_messages(5))
            self.assertEqual(ga.get_loaded_count("g1"), 5)

    def test_get_loaded_count_not_loaded(self):
        """未加载的群返回 0"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertEqual(ga.get_loaded_count("nonexistent"), 0)

    def test_get_data_file_path(self):
        """get_data_file_path 返回正确路径"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            path = ga.get_data_file_path("test_group")
            self.assertTrue(path.endswith("test_group.json"))
            self.assertIn(tmpdir, path)

    def test_get_ai_analyzer(self):
        """get_ai_analyzer 返回分析器"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({"data_dir": tmpdir})
            self.assertIsNotNone(ga.get_ai_analyzer())


if __name__ == "__main__":
    unittest.main()

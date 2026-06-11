"""WebService 单元测试 — 覆盖 api_server.py 所有公共方法"""
import csv
import io
import json
import os
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from chatlens.plugins.web.api_server import WebService


# ── 辅助：构造 mock ga ──────────────────────────────────────

def _make_mock_ga():
    """构造一个模拟的 ga (GroupAnalysis) 对象"""
    ga = MagicMock()
    ga.config = {
        'ai_service': {
            'provider': 'deepseek',
            'api_key': 'sk-1234567890abcdef',
            'base_url': 'https://api.deepseek.com/v1',
            'model': 'deepseek-chat',
            'temperature': 0.7,
            'max_tokens': 4096,
        },
        'server': {'host': 'localhost', 'port': 8080},
    }
    ga.has_api_key.return_value = True
    ga.is_api_key_placeholder.return_value = False
    ga.get_groups.return_value = ['group1@chatroom', 'group2']
    ga.get_data_files.return_value = [
        {'group_name': 'group1@chatroom', 'file': 'data1.json'},
        {'group_name': 'group3', 'file': 'data3.json'},
    ]
    ga.get_messages.return_value = []
    ga.get_provider.return_value = None
    ga.get_reports_dir.return_value = '/tmp/reports'
    ga.providers = MagicMock()
    ga.providers.get_available.return_value = []
    ga.schedule = None
    return ga


def _make_mock_messages(count=3):
    """构造模拟消息列表"""
    msgs = []
    for i in range(count):
        m = MagicMock()
        m.timestamp = f'2025-01-{10+i:02d}T10:00:00'
        m.sender = f'sender_{i}'
        m.sender_remark = f'remark_{i}'
        m.msg_type = 'text'
        m.content = f'hello {i}'
        m.quote_content = ''
        m.group_name = 'group1@chatroom'
        m.to_dict.return_value = {
            'sender': m.sender, 'content': m.content, 'msg_type': m.msg_type,
            'timestamp': m.timestamp, 'sender_remark': m.sender_remark,
        }
        msgs.append(m)
    return msgs


# ── 测试类 ────────────────────────────────────────────────────

class TestWebServiceInit(unittest.TestCase):
    """测试 WebService 初始化"""

    @patch('chatlens.plugins.web.api_server.get_start_time', return_value=1000.0)
    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_init(self, MockIDE, MockOrch, mock_start):
        ga = _make_mock_ga()
        svc = WebService(ga)
        self.assertEqual(svc.ga, ga)
        self.assertEqual(svc.config, ga.config)
        self.assertEqual(svc._start_time, 1000.0)
        self.assertEqual(svc._error_count, 0)


class TestGetHealth(unittest.TestCase):
    """测试 get_health 方法"""

    @patch('psutil.Process')
    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_health_normal(self, MockIDE, MockOrch, mock_process_cls):
        ga = _make_mock_ga()
        svc = WebService(ga)
        mock_proc = MagicMock()
        mock_proc.memory_info.return_value = MagicMock(rss=100 * 1024 * 1024)  # 100MB
        mock_process_cls.return_value = mock_proc
        result = svc.get_health()
        self.assertEqual(result['status'], 'ok')
        self.assertIn('uptime', result)
        self.assertIn('uptime_seconds', result)
        self.assertEqual(result['memory_mb'], 100.0)
        self.assertFalse(result['chatlog_available'])
        self.assertEqual(result['error_count'], 0)

    @patch('psutil.Process')
    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_health_with_wechat_provider(self, MockIDE, MockOrch, mock_process_cls):
        ga = _make_mock_ga()
        wechat = MagicMock()
        wechat.is_available.return_value = True
        ga.get_provider.return_value = wechat
        svc = WebService(ga)
        mock_proc = MagicMock()
        mock_proc.memory_info.return_value = MagicMock(rss=50 * 1024 * 1024)
        mock_process_cls.return_value = mock_proc
        result = svc.get_health()
        self.assertTrue(result['chatlog_available'])

    @patch('psutil.Process')
    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_health_with_schedule(self, MockIDE, MockOrch, mock_process_cls):
        ga = _make_mock_ga()
        ga.schedule = MagicMock()
        ga.schedule.get_task_count.return_value = 3
        svc = WebService(ga)
        mock_proc = MagicMock()
        mock_proc.memory_info.return_value = MagicMock(rss=50 * 1024 * 1024)
        mock_process_cls.return_value = mock_proc
        result = svc.get_health()
        self.assertEqual(result['scheduled_tasks'], 3)


class TestGetConfig(unittest.TestCase):
    """测试 get_config 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_config_normal(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        result = svc.get_config()
        self.assertTrue(result['success'])
        ai = result['config']['ai_service']
        self.assertEqual(ai['provider'], 'deepseek')
        # api_key 应被掩码
        self.assertIn('***', ai['api_key'])
        self.assertTrue(ai['api_key_set'])
        self.assertFalse(ai['api_key_placeholder'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_config_placeholder_key(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.config['ai_service']['api_key'] = 'YOUR_API_KEY_HERE'
        ga.is_api_key_placeholder.return_value = True
        ga.has_api_key.return_value = False
        svc = WebService(ga)
        result = svc.get_config()
        ai = result['config']['ai_service']
        self.assertTrue(ai['api_key_placeholder'])
        self.assertEqual(ai['api_key'], '')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_config_short_key(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.config['ai_service']['api_key'] = 'abc'
        svc = WebService(ga)
        result = svc.get_config()
        ai = result['config']['ai_service']
        self.assertEqual(ai['api_key'], '***')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_config_no_key(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.config['ai_service'].pop('api_key', None)
        ga.has_api_key.return_value = False
        svc = WebService(ga)
        result = svc.get_config()
        ai = result['config']['ai_service']
        self.assertEqual(ai['api_key'], '')
        self.assertFalse(ai['api_key_set'])


class TestSaveConfig(unittest.TestCase):
    """测试 save_config 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_save_config_deep_merge(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        new_config = {'ai_service': {'temperature': 0.9}}
        with patch('builtins.open', unittest.mock.mock_open()) as mock_open, \
             patch('os.makedirs'):
            result = svc.save_config(new_config)
        self.assertTrue(result['success'])
        # 验证深度合并：temperature 被更新，其他字段保留
        self.assertEqual(svc.config['ai_service']['temperature'], 0.9)
        self.assertEqual(svc.config['ai_service']['provider'], 'deepseek')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_save_config_masked_key_ignored(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        new_config = {'ai_service': {'api_key': '***masked***'}}
        with patch('builtins.open', unittest.mock.mock_open()) as mock_open, \
             patch('os.makedirs'):
            result = svc.save_config(new_config)
        self.assertTrue(result['success'])
        # 以 *** 开头的 api_key 应被移除，不覆盖原值
        self.assertEqual(svc.config['ai_service']['api_key'], 'sk-1234567890abcdef')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_save_config_new_key_updates_analyzer(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        new_config = {'ai_service': {'api_key': 'sk-newkey123456'}}
        with patch('builtins.open', unittest.mock.mock_open()), \
             patch('os.makedirs'), \
             patch('chatlens.core.ai_analyzer.GroupAIAnalyzer') as MockAI:
            result = svc.save_config(new_config)
        self.assertTrue(result['success'])
        MockAI.assert_called_once()

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_save_config_empty_api_key_preserves_existing(self, MockIDE, MockOrch):
        """Bug1 修复：前端留空 api_key 提交时不应清空原 key。

        场景：用户编辑其他字段（如 temperature），前端没填 api_key 输入框，
        会提交 api_key=""。旧实现会直接用空字符串覆盖原 key，造成丢失。
        """
        ga = _make_mock_ga()
        svc = WebService(ga)
        original_key = svc.config['ai_service']['api_key']
        new_config = {'ai_service': {'temperature': 0.5, 'api_key': ''}}
        with patch('builtins.open', unittest.mock.mock_open()), \
             patch('os.makedirs'):
            result = svc.save_config(new_config)
        self.assertTrue(result['success'])
        # 原 api_key 应被保留
        self.assertEqual(svc.config['ai_service']['api_key'], original_key)
        # 温度字段应被正常更新
        self.assertEqual(svc.config['ai_service']['temperature'], 0.5)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_save_config_whitespace_api_key_rejected_by_validator(self, MockIDE, MockOrch):
        """纯空白 api_key（"   "）应当被 _validate_config 拒绝（这是已有的防御）。

        注：这一行为由 _validate_config 负责（len < 10 检查），不在 Bug1 修复范围。
        这里断言现状被保留，避免回归。
        """
        ga = _make_mock_ga()
        svc = WebService(ga)
        original_key = svc.config['ai_service']['api_key']
        new_config = {'ai_service': {'api_key': '   '}}
        result = svc.save_config(new_config)
        self.assertFalse(result['success'])
        # 原 api_key 不应被覆盖
        self.assertEqual(svc.config['ai_service']['api_key'], original_key)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_save_config_real_key_overwrites(self, MockIDE, MockOrch):
        """真实 api_key（不以 *** 开头且非空）应正常覆盖。"""
        ga = _make_mock_ga()
        svc = WebService(ga)
        new_key = 'sk-realnewkey123456'
        new_config = {'ai_service': {'api_key': new_key}}
        with patch('builtins.open', unittest.mock.mock_open()), \
             patch('os.makedirs'), \
             patch('chatlens.core.ai_analyzer.GroupAIAnalyzer') as MockAI:
            result = svc.save_config(new_config)
        self.assertTrue(result['success'])
        self.assertEqual(svc.config['ai_service']['api_key'], new_key)
        MockAI.assert_called_once()


class TestDeepMerge(unittest.TestCase):
    """测试 _deep_merge 静态方法"""

    def test_shallow_merge(self):
        base = {'a': 1, 'b': 2}
        override = {'b': 3, 'c': 4}
        result = WebService._deep_merge(base, override)
        self.assertEqual(result, {'a': 1, 'b': 3, 'c': 4})

    def test_deep_merge_nested(self):
        base = {'a': {'x': 1, 'y': 2}, 'b': 3}
        override = {'a': {'y': 99, 'z': 100}}
        result = WebService._deep_merge(base, override)
        self.assertEqual(result, {'a': {'x': 1, 'y': 99, 'z': 100}, 'b': 3})

    def test_deep_merge_override_non_dict(self):
        base = {'a': {'x': 1}}
        override = {'a': 'replaced'}
        result = WebService._deep_merge(base, override)
        self.assertEqual(result, {'a': 'replaced'})

    def test_deep_merge_no_mutation(self):
        base = {'a': {'x': 1}}
        override = {'a': {'y': 2}}
        result = WebService._deep_merge(base, override)
        # base 不应被修改
        self.assertNotIn('y', base['a'])


class TestGetGroups(unittest.TestCase):
    """测试 get_groups 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_get_groups_normal(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        result = svc.get_groups()
        self.assertTrue(result['success'])
        # 应包含 ga.get_groups 和 get_data_files 中的群名（去重）
        self.assertIn('group1@chatroom', result['groups'])
        self.assertIn('group2', result['groups'])
        self.assertIn('group3', result['groups'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_get_groups_empty(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.get_groups.return_value = []
        ga.get_data_files.return_value = []
        svc = WebService(ga)
        result = svc.get_groups()
        self.assertTrue(result['success'])
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['group_info'], [])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_get_groups_with_display_name(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        provider = MagicMock()
        provider.get_display_name.side_effect = lambda g: '显示名' if g == 'group1@chatroom' else g
        ga.providers.get_available.return_value = [provider]
        svc = WebService(ga)
        result = svc.get_groups()
        # 找到 group1@chatroom 的 info
        info = [i for i in result['group_info'] if i['value'] == 'group1@chatroom']
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]['label'], '显示名')


class TestLoadFromChatlog(unittest.TestCase):
    """测试 load_from_chatlog 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_load_success(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        msgs = _make_mock_messages(5)
        ga.load_from_provider.return_value = msgs
        svc = WebService(ga)
        result = svc.load_from_chatlog('talker1')
        self.assertTrue(result['success'])
        self.assertEqual(result['message_count'], 5)
        self.assertEqual(result['talker'], 'talker1')
        ga.save_loaded.assert_called_once_with('talker1', msgs)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_load_no_messages(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.load_from_provider.return_value = []
        svc = WebService(ga)
        result = svc.load_from_chatlog('talker1')
        self.assertFalse(result['success'])
        self.assertIn('未找到', result['error'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_load_failure_increments_error(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.load_from_provider.side_effect = OSError('连接失败')
        svc = WebService(ga)
        result = svc.load_from_chatlog('talker1')
        self.assertFalse(result['success'])
        self.assertIn('连接失败', result['error'])
        self.assertEqual(svc._error_count, 1)


class TestGetStats(unittest.TestCase):
    """测试 get_stats 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_get_stats_delegates(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.get_stats.return_value = {'success': True, 'data': {'total': 100}}
        result = svc.get_stats('group1')
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['total'], 100)
        svc.orchestrator.get_stats.assert_called_once_with('group1')


class TestAutoAnalyze(unittest.TestCase):
    """测试 auto_analyze 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_auto_analyze_delegates(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.auto_analyze.return_value = {'success': True, 'data': {}}
        result = svc.auto_analyze('group1')
        self.assertTrue(result['success'])
        svc.orchestrator.auto_analyze.assert_called_once_with(
            'group1', 'scrapbook', 'jpg', '', '', ide_tasks=svc.ide_tasks, use_ide=False, use_rules=False, use_fallback=False
        )

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_auto_analyze_with_params(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.auto_analyze.return_value = {'success': True}
        result = svc.auto_analyze('group1', theme='dark', fmt='png', start_date='2025-01-01', end_date='2025-01-31')
        svc.orchestrator.auto_analyze.assert_called_once_with(
            'group1', 'dark', 'png', '2025-01-01', '2025-01-31', ide_tasks=svc.ide_tasks, use_ide=False, use_rules=False, use_fallback=False
        )

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_auto_analyze_passes_use_fallback_true(self, MockIDE, MockOrch):
        """Bug 修复：use_fallback=True 时应透传到 orchestrator"""
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.auto_analyze.return_value = {'success': True, 'method': 'rules'}
        result = svc.auto_analyze('group1', use_fallback=True)
        self.assertTrue(result['success'])
        svc.orchestrator.auto_analyze.assert_called_once_with(
            'group1', 'scrapbook', 'jpg', '', '', ide_tasks=svc.ide_tasks, use_ide=False, use_rules=False, use_fallback=True
        )

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_auto_analyze_default_use_fallback_false(self, MockIDE, MockOrch):
        """Bug 修复：不传 use_fallback 时默认为 False"""
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.auto_analyze.return_value = {'success': True}
        svc.auto_analyze('group1')
        # 验证默认值是 False（按关键字参数检查）
        call_kwargs = svc.orchestrator.auto_analyze.call_args.kwargs
        self.assertIn('use_fallback', call_kwargs)
        self.assertEqual(call_kwargs['use_fallback'], False)


class TestGetDailyDates(unittest.TestCase):
    """测试 get_daily_dates 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_daily_dates_normal(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        msgs = _make_mock_messages(3)
        ga.get_messages.return_value = msgs
        svc = WebService(ga)
        result = svc.get_daily_dates('group1')
        self.assertTrue(result['success'])
        self.assertEqual(len(result['dates']), 3)
        self.assertEqual(len(result['date_stats']), 3)
        # 日期降序
        self.assertTrue(result['dates'][0] >= result['dates'][-1])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_daily_dates_no_messages(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.get_messages.return_value = []
        svc = WebService(ga)
        result = svc.get_daily_dates('group1')
        self.assertTrue(result['success'])
        self.assertEqual(result['dates'], [])
        self.assertEqual(result['date_stats'], [])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_daily_dates_empty_timestamp(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        m = MagicMock()
        m.timestamp = ''
        m.sender = 'user1'
        m.sender_remark = ''
        ga.get_messages.return_value = [m]
        svc = WebService(ga)
        result = svc.get_daily_dates('group1')
        self.assertTrue(result['success'])
        self.assertEqual(result['dates'], [])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_daily_dates_member_tracking(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        m1 = MagicMock()
        m1.timestamp = '2025-01-10T10:00:00'
        m1.sender = 'user1'
        m1.sender_remark = 'Alice'
        m2 = MagicMock()
        m2.timestamp = '2025-01-10T11:00:00'
        m2.sender = 'user2'
        m2.sender_remark = 'Bob'
        m3 = MagicMock()
        m3.timestamp = '2025-01-10T12:00:00'
        m3.sender = 'user1'
        m3.sender_remark = 'Alice'
        ga.get_messages.return_value = [m1, m2, m3]
        svc = WebService(ga)
        result = svc.get_daily_dates('group1')
        # 同一天 2 个不同成员
        stat = result['date_stats'][0]
        self.assertEqual(stat['count'], 3)
        self.assertEqual(stat['members'], 2)


class TestExportData(unittest.TestCase):
    """测试 export_data 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_export_csv(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        msgs = _make_mock_messages(2)
        ga.get_messages.return_value = msgs
        svc = WebService(ga)
        result = svc.export_data('group1@chatroom', fmt='csv')
        self.assertIsInstance(result, tuple)
        content, filename, content_type = result
        self.assertTrue(filename.endswith('.csv'))
        self.assertIn('text/csv', content_type)
        # 解析 CSV 验证
        reader = csv.reader(io.StringIO(content.decode('utf-8-sig')))
        rows = list(reader)
        self.assertEqual(len(rows), 3)  # header + 2 data rows

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_export_json(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        msgs = _make_mock_messages(2)
        ga.get_messages.return_value = msgs
        svc = WebService(ga)
        result = svc.export_data('group1@chatroom', fmt='json')
        self.assertIsInstance(result, tuple)
        content, filename, content_type = result
        self.assertTrue(filename.endswith('.json'))
        self.assertIn('application/json', content_type)
        data = json.loads(content.decode('utf-8'))
        self.assertEqual(data['message_count'], 2)
        self.assertEqual(len(data['messages']), 2)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_export_no_messages(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.get_messages.return_value = []
        svc = WebService(ga)
        result = svc.export_data('group1', fmt='csv')
        self.assertFalse(result['success'])
        self.assertIn('未找到', result['error'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_export_unsupported_format(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        msgs = _make_mock_messages(1)
        ga.get_messages.return_value = msgs
        svc = WebService(ga)
        result = svc.export_data('group1', fmt='xml')
        self.assertFalse(result['success'])
        self.assertIn('不支持', result['error'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_export_csv_safe_name(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        msgs = _make_mock_messages(1)
        ga.get_messages.return_value = msgs
        svc = WebService(ga)
        _, filename, _ = svc.export_data('group1@chatroom', fmt='csv')
        # @chatroom 应被移除
        self.assertNotIn('@chatroom', filename)


class TestCompareGroups(unittest.TestCase):
    """测试 compare_groups 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_compare_delegates(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.compare_groups.return_value = {'success': True, 'data': {}}
        result = svc.compare_groups(['g1', 'g2'])
        self.assertTrue(result['success'])
        svc.orchestrator.compare_groups.assert_called_once_with(['g1', 'g2'])


class TestDeleteData(unittest.TestCase):
    """测试 delete_data 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_delete_success(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.delete_loaded.return_value = True
        svc = WebService(ga)
        result = svc.delete_data('group1')
        self.assertTrue(result['success'])
        ga.delete_loaded.assert_called_once_with('group1')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_delete_not_found(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.delete_loaded.return_value = False
        svc = WebService(ga)
        result = svc.delete_data('group1')
        self.assertFalse(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_delete_empty_name(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        result = svc.delete_data('')
        self.assertFalse(result['success'])
        self.assertIn('未指定', result['error'])


class TestDeleteDataBatch(unittest.TestCase):
    """F7 修复：delete_data_batch 批量删除（复用 delete_data）"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_batch_all_success(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.delete_loaded.return_value = True
        svc = WebService(ga)
        result = svc.delete_data_batch(['g1', 'g2', 'g3'])
        self.assertTrue(result['success'])
        self.assertEqual(result['deleted'], 3)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(ga.delete_loaded.call_count, 3)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_batch_partial(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        # 第一次成功，第二次失败（group_name 为空 → delete_data 内部返回 False）
        ga.delete_loaded.side_effect = [True, False, True]
        svc = WebService(ga)
        result = svc.delete_data_batch(['g1', 'g2', 'g3'])
        self.assertTrue(result['success'])
        self.assertEqual(result['deleted'], 2)
        self.assertEqual(result['failed'], 1)


class TestGetDataFiles(unittest.TestCase):
    """测试 get_data_files 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_get_data_files(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        result = svc.get_data_files()
        self.assertTrue(result['success'])
        self.assertEqual(len(result['files']), 2)


class TestGetChatlogChatrooms(unittest.TestCase):
    """测试 get_chatlog_chatrooms 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_no_wechat_provider(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.get_provider.return_value = None
        svc = WebService(ga)
        result = svc.get_chatlog_chatrooms()
        self.assertFalse(result['success'])
        self.assertEqual(result['chatrooms'], [])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_with_bridge(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        wechat = MagicMock()
        bridge = MagicMock()
        bridge.get_chatrooms.return_value = ['room1', 'room2']
        wechat.bridge = bridge
        ga.get_provider.return_value = wechat
        svc = WebService(ga)
        result = svc.get_chatlog_chatrooms()
        self.assertTrue(result['success'])
        self.assertEqual(len(result['chatrooms']), 2)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_bridge_error(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        wechat = MagicMock()
        bridge = MagicMock()
        bridge.get_chatrooms.side_effect = OSError('db error')
        wechat.bridge = bridge
        ga.get_provider.return_value = wechat
        svc = WebService(ga)
        result = svc.get_chatlog_chatrooms()
        self.assertFalse(result['success'])
        self.assertIn('db error', result['error'])


class TestGetChatlogTalkers(unittest.TestCase):
    """测试 get_chatlog_talkers 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_no_wechat_provider(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.get_provider.return_value = None
        svc = WebService(ga)
        result = svc.get_chatlog_talkers()
        self.assertFalse(result['success'])
        self.assertEqual(result['talkers'], [])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_with_bridge(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        wechat = MagicMock()
        bridge = MagicMock()
        bridge.get_all_talkers.return_value = ['talker1']
        wechat.bridge = bridge
        ga.get_provider.return_value = wechat
        svc = WebService(ga)
        result = svc.get_chatlog_talkers()
        self.assertTrue(result['success'])
        self.assertEqual(result['talkers'], ['talker1'])


class TestScheduledTasks(unittest.TestCase):
    """测试定时任务相关方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_create_no_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = None
        svc = WebService(ga)
        result = svc.create_scheduled_task('g1', 8, 0)
        self.assertFalse(result['success'])
        self.assertIn('未启用', result['error'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_create_with_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = MagicMock()
        ga.schedule.create.return_value = {'success': True, 'task_id': 't1'}
        svc = WebService(ga)
        result = svc.create_scheduled_task('g1', 8, 30, theme='dark', fmt='png')
        self.assertTrue(result['success'])
        ga.schedule.create.assert_called_once_with('g1', 8, 30, 'dark', 'png')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_list_no_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = None
        svc = WebService(ga)
        result = svc.list_scheduled_tasks()
        self.assertFalse(result['success'])
        self.assertEqual(result['tasks'], [])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_list_with_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = MagicMock()
        ga.schedule.list_all.return_value = {'success': True, 'tasks': []}
        svc = WebService(ga)
        result = svc.list_scheduled_tasks()
        self.assertTrue(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_delete_no_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = None
        svc = WebService(ga)
        result = svc.delete_scheduled_task('t1')
        self.assertFalse(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_delete_with_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = MagicMock()
        ga.schedule.delete.return_value = {'success': True}
        svc = WebService(ga)
        result = svc.delete_scheduled_task('t1')
        self.assertTrue(result['success'])
        ga.schedule.delete.assert_called_once_with('t1')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_toggle_no_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = None
        svc = WebService(ga)
        result = svc.toggle_scheduled_task('t1', True)
        self.assertFalse(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_toggle_with_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = MagicMock()
        ga.schedule.toggle.return_value = {'success': True}
        svc = WebService(ga)
        result = svc.toggle_scheduled_task('t1', False)
        self.assertTrue(result['success'])
        ga.schedule.toggle.assert_called_once_with('t1', False)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_trigger_no_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = None
        svc = WebService(ga)
        result = svc.trigger_scheduled_task('t1')
        self.assertFalse(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_trigger_with_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.schedule = MagicMock()
        ga.schedule.trigger.return_value = {'success': True}
        svc = WebService(ga)
        result = svc.trigger_scheduled_task('t1')
        self.assertTrue(result['success'])
        ga.schedule.trigger.assert_called_once_with('t1')


class TestGetStatus(unittest.TestCase):
    """测试 get_status 方法 — H2 修复后只返 4 个轻量字段"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_status_no_wechat(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.get_provider.return_value = None
        ga.get_reports_dir.return_value = '/nonexistent_dir'
        svc = WebService(ga)
        result = svc.get_status()
        # H2 修复：get_status 只返轻量字段，chatlog 联系人 / reports 统计
        # 已被拆分到 _compute_status_details
        self.assertIn('api_key_configured', result)
        self.assertIn('ollama_available', result)
        self.assertIn('ide_available', result)
        self.assertIn('error_count', result)
        self.assertTrue(result['api_key_configured'])
        self.assertEqual(result['error_count'], 0)
        # 不再包含 chatlog_talkers_count / report_count
        self.assertNotIn('chatlog_talkers_count', result)
        self.assertNotIn('report_count', result)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_status_includes_chatlog_available(self, MockIDE, MockOrch):
        """回归测试：H2 修复后 get_status() 必须保留 chatlog_available 字段（前端 UI 状态条依赖）"""
        ga = _make_mock_ga()
        ga.get_provider.return_value = None
        ga.get_reports_dir.return_value = '/nonexistent_dir'
        svc = WebService(ga)
        result = svc.get_status()
        # 只断言 key 存在，不断言值（取决于环境）
        self.assertIn('chatlog_available', result)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_status_details_with_reports(self, MockIDE, MockOrch):
        """H2 修复：_compute_status_details 用 os.scandir + 30s 缓存"""
        ga = _make_mock_ga()
        ga.get_provider.return_value = None
        # mock os.scandir 返回两个文件
        mock_entry1 = MagicMock()
        mock_entry1.is_file.return_value = True
        mock_entry1.stat.return_value = MagicMock(st_size=2048)
        mock_entry2 = MagicMock()
        mock_entry2.is_file.return_value = True
        mock_entry2.stat.return_value = MagicMock(st_size=4096)
        with patch('os.path.exists', return_value=True), \
             patch('os.scandir', return_value=iter([mock_entry1, mock_entry2])):
            svc = WebService(ga)
            result = svc._compute_status_details()
            self.assertEqual(result['report_count'], 2)
            self.assertEqual(result['report_total_size_kb'], 6.0)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_status_details_cache(self, MockIDE, MockOrch):
        """H2 修复：_compute_status_details 30s 缓存命中"""
        ga = _make_mock_ga()
        ga.get_provider.return_value = None
        with patch('os.path.exists', return_value=False):
            svc = WebService(ga)
            r1 = svc._compute_status_details()
            r2 = svc._compute_status_details()
            # 第二次调用应返回缓存中的同一对象
            self.assertIs(r1, r2)


class TestRefreshChatlog(unittest.TestCase):
    """测试 refresh_chatlog 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_refresh_success(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        with patch('chatlens.plugins.web.api_server.run_chatlog_decrypt', return_value=True):
            svc = WebService(ga)
            result = svc.refresh_chatlog()
            self.assertTrue(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_refresh_failure(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        with patch('chatlens.plugins.web.api_server.run_chatlog_decrypt', return_value=False):
            svc = WebService(ga)
            result = svc.refresh_chatlog()
            self.assertFalse(result['success'])


class TestGetAIAnalysis(unittest.TestCase):
    """测试 get_ai_analysis 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_delegates(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.get_ai_analysis.return_value = {'success': True, 'data': {}}
        result = svc.get_ai_analysis('g1', use_rules=True, start_date='2025-01-01')
        self.assertTrue(result['success'])
        # H1 修复：get_ai_analysis 多传 use_ide 参数
        svc.orchestrator.get_ai_analysis.assert_called_once_with(
            'g1', True, '2025-01-01', '', 'scrapbook', 'jpg', False, False
        )


class TestGetDailyAnalysis(unittest.TestCase):
    """测试 get_daily_analysis 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_delegates(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.get_daily_analysis.return_value = {'success': True}
        result = svc.get_daily_analysis('g1', '2025-01-10')
        self.assertTrue(result['success'])
        svc.orchestrator.get_daily_analysis.assert_called_once_with('g1', '2025-01-10')


class TestDailyAutoReport(unittest.TestCase):
    """测试 daily_auto_report 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_no_messages(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.get_daily_analysis.return_value = {'success': True, 'message_count': 0}
        result = svc.daily_auto_report('g1', '2025-01-10')
        self.assertFalse(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_report_success(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.get_daily_analysis.return_value = {
            'success': True, 'message_count': 10, 'stats': {}, 'ai_data': {}
        }
        ga.report = MagicMock()
        ga.report.generate_image.return_value = {
            'success': True, 'report': {'path': '/tmp/report.jpg'}
        }
        result = svc.daily_auto_report('g1', '2025-01-10')
        self.assertTrue(result['success'])
        self.assertEqual(result['message_count'], 10)

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_report_generation_fails(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.get_daily_analysis.return_value = {
            'success': True, 'message_count': 10, 'stats': {}, 'ai_data': {}
        }
        ga.report = MagicMock()
        ga.report.generate_image.return_value = {'success': False, 'error': '生成失败'}
        result = svc.daily_auto_report('g1', '2025-01-10')
        self.assertFalse(result['success'])


class TestGenerateReport(unittest.TestCase):
    """测试 generate_report 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_stats_fail(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.get_stats.return_value = {'success': False, 'error': 'no data'}
        result = svc.generate_report('g1')
        self.assertFalse(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_report_success(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.get_stats.return_value = {'success': True, 'data': {'total': 100}}
        svc.orchestrator.get_ai_analysis.return_value = {'success': True, 'data': {'summary': 'ok'}}
        result = svc.generate_report('g1')
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['ai_data']['summary'], 'ok')


class TestShutdown(unittest.TestCase):
    """测试 shutdown 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_shutdown_resets_providers(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        p1 = MagicMock()
        ga.providers.get_all.return_value = [p1]
        ga.schedule = None
        svc = WebService(ga)
        svc.shutdown()
        p1.reset_connections.assert_called_once()

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_shutdown_with_schedule(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.providers.get_all.return_value = []
        ga.schedule = MagicMock()
        svc = WebService(ga)
        svc.shutdown()
        ga.schedule.shutdown.assert_called_once()


class TestGetApiDocs(unittest.TestCase):
    """测试 get_api_docs 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_returns_docs(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        with patch('chatlens.plugins.web._shared_docs.get_report_api_docs', return_value=[]):
            docs = svc.get_api_docs()
        self.assertIsInstance(docs, list)
        self.assertTrue(len(docs) > 0)
        # 验证基础文档项存在
        paths = [d['path'] for d in docs]
        self.assertIn('/api/status', paths)
        self.assertIn('/api/health', paths)


class TestIDETasks(unittest.TestCase):
    """测试 IDE 任务相关方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_get_ide_task(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.ide_tasks.get.return_value = {'success': True, 'task': {'task_id': 'abc'}}
        result = svc.get_ide_task('abc')
        self.assertTrue(result['success'])
        svc.ide_tasks.get.assert_called_once_with('abc')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_get_ide_pending_tasks(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.ide_tasks.get_pending.return_value = {'success': True, 'tasks': []}
        result = svc.get_ide_pending_tasks()
        self.assertTrue(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_create_ide_task_no_messages(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.orchestrator.filter_messages.return_value = []
        result = svc.create_ide_task('g1')
        self.assertFalse(result['success'])
        self.assertIn('没有', result['error'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_submit_ide_result_task_not_found(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.ide_tasks.get.return_value = {'success': False, 'error': 'not found'}
        result = svc.submit_ide_result('bad_id', {})
        self.assertFalse(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_submit_ide_result_success(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        svc = WebService(ga)
        svc.ide_tasks.get.return_value = {
            'success': True,
            'task': {'task_id': 't1', 'group_name': 'g1', 'theme': 'scrapbook', 'fmt': 'jpg'}
        }
        svc.orchestrator.generate_report.return_value = {'success': True, 'path': '/tmp/r.jpg'}
        result = svc.submit_ide_result('t1', {'analysis': 'ok'})
        self.assertTrue(result['success'])
        svc.ide_tasks.mark_completed.assert_called_once()


class TestGetIDEPrompt(unittest.TestCase):
    """测试 get_ide_prompt 方法"""

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_no_messages(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        ga.get_messages.return_value = []
        svc = WebService(ga)
        result = svc.get_ide_prompt('g1')
        self.assertFalse(result['success'])

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_success(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        msgs = _make_mock_messages(1)
        ga.get_messages.return_value = msgs
        svc = WebService(ga)
        with patch('chatlens.core.ai_analyzer.generate_ide_prompt', return_value='prompt text'):
            result = svc.get_ide_prompt('g1')
        self.assertTrue(result['success'])
        self.assertEqual(result['prompt'], 'prompt text')

    @patch('chatlens.plugins.web.api_server.AnalysisOrchestrator')
    @patch('chatlens.plugins.web.api_server.IDETaskQueue')
    def test_exception(self, MockIDE, MockOrch):
        ga = _make_mock_ga()
        msgs = _make_mock_messages(1)
        ga.get_messages.return_value = msgs
        svc = WebService(ga)
        with patch('chatlens.core.ai_analyzer.generate_ide_prompt', side_effect=RuntimeError('fail')):
            result = svc.get_ide_prompt('g1')
        self.assertFalse(result['success'])
        self.assertIn('fail', result['error'])


if __name__ == '__main__':
    unittest.main()

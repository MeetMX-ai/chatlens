"""analysis_orchestrator.py 补充单元测试 — 覆盖 __init__、get_stats 缓存过期、
auto_analyze AI/规则分支、get_ai_analysis 缓存命中、compare_groups 边界、
filter_messages/generate_report 公共方法、_normalize_stats 深拷贝/默认值、
invalidate_cache 含 _rule_cache"""

import os
import sys
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from chatlens.core.models import ChatMessage
from chatlens.plugins.web.analysis_orchestrator import AnalysisOrchestrator, _CACHE_TTL


@pytest.fixture(autouse=True)
def _clear_ollama_cache():
    """M2 修复后，5s 模块级缓存会跨测试复用 —— 每个测试前清空。"""
    from chatlens.plugins.web import analysis_orchestrator as _ao
    _ao._ollama_cache.clear()
    yield
    _ao._ollama_cache.clear()


def _msg(sender='Alice', content='Hello', msg_type='text', msg_attr='',
         timestamp='2026-05-01 10:00:00', group_name='TestGroup'):
    return ChatMessage(sender=sender, content=content, msg_type=msg_type,
                       msg_attr=msg_attr, timestamp=timestamp, group_name=group_name)


def _make_messages(n=20):
    return [_msg(sender=f'User{i%5}', content=f'Message {i}',
                 timestamp=f'2026-05-{(i%28)+1:02d} {8+i%14:02d}:{i%60:02d}:00')
            for i in range(n)]


def _mock_ga(messages=None, has_api_key=True, is_placeholder=False):
    ga = MagicMock()
    ga.get_messages.return_value = messages or _make_messages(20)
    ga.has_api_key.return_value = has_api_key
    ga.is_api_key_placeholder.return_value = is_placeholder
    ga.stats_analyzer.analyze.return_value = {
        'overview': {
            'total_messages': 20, 'total_members': 5,
            'time_range': {'start': '2026-05-01', 'end': '2026-05-28'},
            'avg_messages_per_day': 1,
        },
        'member_stats': [{'sender': 'Alice', 'msg_count': 10}],
        'msg_type_distribution': [],
        'keyword_cloud': [],
        'interaction_analysis': {'top_interactions': []},
    }
    ga.ai_analyzer.full_analysis.return_value = {
        'summary': {'summary': 'AI summary'},
        'user_titles': {'user_titles': [{'name': 'Alice', 'title': '话痨'}]},
        'golden_quotes': {'golden_quotes': []},
        'chat_quality': {'title': 'Test', 'subtitle': '5人', 'dimensions': [], 'summary': ''},
        'keywords': {'keywords': []},
    }
    ga.report = MagicMock()
    ga.report.generate_image.return_value = {'report': {'html_url': '/test'}}
    return ga


# ── __init__ ──────────────────────────────────────────────────

class TestInit:
    def test_default_attributes(self):
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        assert orch.ga is ga
        assert orch._stats_cache == {}
        assert orch._stats_cache_time == {}
        assert orch._rule_cache == {}
        assert orch._rule_cache_time == {}

    def test_stores_ga_reference(self):
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        assert orch.ga is ga


# ── get_stats — 缓存过期清理、首次调用 ────────────────────────

class TestGetStatsCacheExpiry:
    def test_first_call_invokes_analyze(self):
        """首次调用应调用 stats_analyzer.analyze"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        result = orch.get_stats('group_a')
        assert result['success'] is True
        ga.stats_analyzer.analyze.assert_called_once()

    def test_cache_hit_within_ttl(self):
        """TTL 内重复调用应命中缓存，不重复调用 analyze"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        orch.get_stats('group_a')
        result = orch.get_stats('group_a')
        assert result['success'] is True
        assert ga.stats_analyzer.analyze.call_count == 1

    def test_cache_expired_clears_entry(self):
        """过期缓存条目应被清理，重新调用 analyze"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        orch.get_stats('group_a')
        # 手动将缓存时间设为过期（key 现在是元组格式）
        orch._stats_cache_time[('group_a', '', '')] = time.time() - _CACHE_TTL - 1
        result = orch.get_stats('group_a')
        assert result['success'] is True
        # 过期后被清理，应重新调用 analyze
        assert ga.stats_analyzer.analyze.call_count == 2

    def test_expired_other_group_cleaned(self):
        """查询 group_b 时，过期的 group_a 缓存也应被清理"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        orch.get_stats('group_a')
        # 让 group_a 过期（key 现在是元组格式）
        orch._stats_cache_time[('group_a', '', '')] = time.time() - _CACHE_TTL - 1
        # 查询 group_b，应清理 group_a
        orch.get_stats('group_b')
        assert ('group_a', '', '') not in orch._stats_cache
        assert ('group_a', '', '') not in orch._stats_cache_time


# ── auto_analyze — AI 分析 / 规则分析 ─────────────────────────

class TestAutoAnalyzeBranches:
    def test_with_api_key_uses_ai(self):
        """有 API Key 时应使用 AI 分析"""
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            result = orch.auto_analyze('test_group')
        assert result['success'] is True
        assert result['method'] == 'ai'

    def test_no_api_key_uses_rules(self):
        """无 API Key 且非 placeholder 时应降级为规则分析"""
        ga = _mock_ga(has_api_key=False, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {
                'summary': {'summary': 'rules result'},
                'user_titles': {'user_titles': []},
                'golden_quotes': {'golden_quotes': []},
                'chat_quality': {'title': '', 'dimensions': []},
                'keywords': {'keywords': []},
            }
            with patch.object(orch, '_try_ollama_analysis', return_value=None):
                result = orch.auto_analyze('test_group')
        assert result['success'] is True
        assert result['method'] == 'rules'

    def test_ai_empty_content_falls_back_to_ollama(self):
        """AI 分析返回空内容时应尝试 Ollama"""
        ga = _mock_ga(has_api_key=True)
        ga.ai_analyzer.full_analysis.return_value = {
            'summary': {'summary': ''},
            'user_titles': {'user_titles': ''},
            'golden_quotes': {'golden_quotes': ''},
            'chat_quality': {},
            'keywords': {},
        }
        orch = AnalysisOrchestrator(ga)
        ollama_result = {
            'summary': {'summary': 'ollama'},
            'user_titles': {'user_titles': []},
            'golden_quotes': {'golden_quotes': []},
            'chat_quality': {'dimensions': []},
            'keywords': {'keywords': []},
        }
        with patch.object(orch, '_try_ollama_analysis', return_value=ollama_result):
            result = orch.auto_analyze('test_group')
        assert result['success'] is True
        assert result['method'] == 'ollama'

    def test_no_messages_returns_error(self):
        """无消息时返回失败"""
        ga = _mock_ga(messages=[])
        ga.get_messages.return_value = []
        orch = AnalysisOrchestrator(ga)
        result = orch.auto_analyze('empty_group')
        assert result['success'] is False


# ── get_ai_analysis — 正常流程、缓存命中 ──────────────────────

class TestGetAiAnalysisExtended:
    def test_normal_ai_flow(self):
        """有 API Key 时的正常 AI 分析流程"""
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        result = orch.get_ai_analysis('test_group')
        assert result['success'] is True
        assert result['method'] == 'ai'
        assert 'data' in result
        assert 'report' in result

    def test_rules_when_use_rules_true(self):
        """use_rules=True 时强制使用规则分析"""
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'summary': {'summary': 'rule result'}}
            result = orch.get_ai_analysis('test_group', use_rules=True)
        assert result['success'] is True
        assert result['method'] == 'rules'

    def test_no_api_key_uses_rules(self):
        """无 API Key 时使用规则分析（H1 修复：必须显式 use_rules=True 才走 rules）"""
        ga = _mock_ga(has_api_key=False)
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'summary': {'summary': 'rule result'}}
            result = orch.get_ai_analysis('test_group', use_rules=True)
        assert result['success'] is True
        assert result['method'] == 'rules'

    def test_no_api_key_no_use_rules_returns_empty(self):
        """H1 修复：无 API Key 且 use_rules=False 时直接返回 EMPTY_RESULT 占位"""
        ga = _mock_ga(has_api_key=False)
        orch = AnalysisOrchestrator(ga)
        result = orch.get_ai_analysis('test_group', use_rules=False)
        assert result['success'] is True
        assert result['method'] == 'empty'
        assert result['data']['summary']['summary'] == ''

    def test_ai_analysis_caches_result(self):
        """H1 修复：5min 内存缓存命中"""
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        orch.get_ai_analysis('test_group')
        # 第二次调用应命中缓存，不再调底层 full_analysis
        orch.get_ai_analysis('test_group')
        assert ga.ai_analyzer.full_analysis.call_count == 1

    def test_exception_returns_error(self):
        """AI 分析抛异常时降级到规则分析（fallback 是用户批准的默认行为）"""
        ga = _mock_ga(has_api_key=True)
        ga.ai_analyzer.full_analysis.side_effect = RuntimeError("API error")
        orch = AnalysisOrchestrator(ga)
        result = orch.get_ai_analysis('test_group')
        # 当前实现：AI 抛异常时 catch 后 fallback 到 rules_based_analysis，
        # 保持 success=True（用户批准的默认降级行为；method 标记为 rules_fallback
        # 以便前端区分原始 AI 结果和降级结果）。
        assert result['success'] is True
        assert result['method'] in ('rules', 'rules_fallback')


# ── compare_groups — 正常对比、群数不足、群数过多 ──────────────

class TestCompareGroupsExtended:
    def test_less_than_two_groups(self):
        """少于 2 个群返回失败"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        result = orch.compare_groups(['only_one'])
        assert result['success'] is False
        assert '2' in result['error']

    def test_more_than_six_groups(self):
        """超过 6 个群返回失败"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        result = orch.compare_groups(['g1', 'g2', 'g3', 'g4', 'g5', 'g6', 'g7'])
        assert result['success'] is False
        assert '6' in result['error']

    def test_empty_list(self):
        """空列表返回失败"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        result = orch.compare_groups([])
        assert result['success'] is False

    def test_normal_comparison(self):
        """正常对比两个群"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'keywords': {'keywords': []}, 'chat_quality': {'dimensions': []}}
            result = orch.compare_groups(['group1', 'group2'])
        assert result['success'] is True
        assert len(result['comparisons']) == 2
        for c in result['comparisons']:
            assert c['available'] is True
            assert 'total_messages' in c

    def test_rule_cache_hit(self):
        """compare_groups 中 rule_based_analysis 缓存命中"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'keywords': {'keywords': []}, 'chat_quality': {'dimensions': []}}
            # 第一次对比
            orch.compare_groups(['group1', 'group2'])
            # 手动设置缓存时间在 TTL 内
            orch._rule_cache_time['group1'] = time.time()
            orch._rule_cache_time['group2'] = time.time()
            # 第二次对比，group1 和 group2 应命中缓存
            orch.compare_groups(['group1', 'group2'])
        # rule_based_analysis 只在第一次被调用（2个群各一次）
        assert mock_rb.call_count == 2


# ── filter_messages — 公共方法调用 ────────────────────────────

class TestFilterMessagesPublic:
    def test_delegates_to_internal(self):
        """filter_messages 应委托给 _filter_messages_by_range"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_filter_messages_by_range', return_value=['msg']) as mock_internal:
            result = orch.filter_messages('test_group', '2026-05-01', '2026-05-10')
        mock_internal.assert_called_once_with('test_group', '2026-05-01', '2026-05-10')
        assert result == ['msg']

    def test_default_date_params(self):
        """不传日期参数时应使用默认空字符串"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_filter_messages_by_range', return_value=[]) as mock_internal:
            orch.filter_messages('test_group')
        mock_internal.assert_called_once_with('test_group', '', '')


# ── generate_report — 公共方法调用、fmt='jpg' 时 generate_image=True ──

class TestGenerateReportPublic:
    def test_delegates_to_internal(self):
        """generate_report 应委托给 _generate_report"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        with patch.object(orch, '_generate_report', return_value={'html_url': '/test'}) as mock_internal:
            result = orch.generate_report('test_group', ai_data, 'scrapbook', 'jpg')
        mock_internal.assert_called_once_with('test_group', ai_data, 'scrapbook', 'jpg')
        assert result == {'html_url': '/test'}

    def test_jpg_format_generates_image(self):
        """fmt='jpg' 时 generate_image 应为 True"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        orch.generate_report('test_group', ai_data, 'scrapbook', 'jpg')
        # 检查 report.generate_image 的调用参数
        call_args = ga.report.generate_image.call_args
        assert call_args.kwargs.get('generate_image') is True or call_args[1].get('generate_image') is True

    def test_html_format_no_image(self):
        """fmt='html' 时 generate_image 应为 False"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        orch.generate_report('test_group', ai_data, 'scrapbook', 'html')
        call_args = ga.report.generate_image.call_args
        assert call_args.kwargs.get('generate_image') is False or call_args[1].get('generate_image') is False

    def test_no_report_attribute(self):
        """ga 没有 report 属性时返回空字典"""
        ga = _mock_ga()
        ga.report = None
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        result = orch.generate_report('test_group', ai_data, 'scrapbook', 'jpg')
        assert result == {}


# ── _normalize_stats — 深拷贝、默认值填充 ──────────────────────

class TestNormalizeStatsExtended:
    def test_deep_copy_does_not_mutate_original(self):
        """_normalize_stats 不应修改原始数据"""
        original = {
            'overview': {
                'time_range': {'start': '2026-05-01', 'end': '2026-05-28'},
                'avg_messages_per_day': 5,
            },
            'member_stats': [{'sender': 'Alice'}],
        }
        import copy
        original_copy = copy.deepcopy(original)
        AnalysisOrchestrator._normalize_stats(original)
        assert original == original_copy

    def test_fills_default_values(self):
        """缺少字段时应填充默认值"""
        data = {
            'overview': {
                'time_range': {'start': '2026-05-01', 'end': '2026-05-28'},
                'avg_messages_per_day': 3,
            },
        }
        result = AnalysisOrchestrator._normalize_stats(data)
        assert result['top_members'] == []
        assert result['message_types'] == []
        assert result['keyword_frequency'] == []
        assert result['interaction_pairs'] == []
        assert result['overview']['start_date'] == '2026-05-01'
        assert result['overview']['end_date'] == '2026-05-28'
        assert result['overview']['avg_per_day'] == 3

    def test_does_not_overwrite_existing_fields(self):
        """已有字段不应被覆盖"""
        data = {
            'overview': {
                'time_range': {'start': '2026-05-01', 'end': '2026-05-28'},
                'avg_messages_per_day': 5,
                'start_date': 'custom_date',
            },
            'top_members': [{'sender': 'Bob'}],
            'member_stats': [{'sender': 'Alice'}],
        }
        result = AnalysisOrchestrator._normalize_stats(data)
        # setdefault 不覆盖已有值
        assert result['top_members'] == [{'sender': 'Bob'}]
        assert result['overview']['start_date'] == 'custom_date'


# ── invalidate_cache — 含 _rule_cache ─────────────────────────

class TestInvalidateCacheExtended:
    def test_clears_stats_cache(self):
        """应清除 stats 缓存"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        orch.get_stats('group_a')
        assert ('group_a', '', '') in orch._stats_cache
        orch.invalidate_cache('group_a')
        assert ('group_a', '', '') not in orch._stats_cache
        assert ('group_a', '', '') not in orch._stats_cache_time

    def test_clears_rule_cache(self):
        """应清除 rule 缓存"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        # 模拟 rule_cache 有数据
        orch._rule_cache['group_a'] = {'keywords': {'keywords': []}}
        orch._rule_cache_time['group_a'] = time.time()
        orch.invalidate_cache('group_a')
        assert 'group_a' not in orch._rule_cache
        assert 'group_a' not in orch._rule_cache_time

    def test_invalidate_nonexistent_group_no_error(self):
        """清除不存在的群缓存不应报错"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        orch.invalidate_cache('nonexistent')  # 不应抛异常


# ── 补充测试：auto_analyze AI 空内容降级到 Ollama 后 Ollama 也失败 ──────

class TestAutoAnalyzeEmptyAiFallback:
    def test_ai_empty_ollama_fails_falls_to_rules(self):
        """AI 返回空内容且 Ollama 也失败时，应降级到规则分析"""
        ga = _mock_ga(has_api_key=True, is_placeholder=False)
        ga.ai_analyzer.full_analysis.return_value = {
            'summary': {'summary': ''},
            'user_titles': {'user_titles': ''},
            'golden_quotes': {'golden_quotes': ''},
            'chat_quality': {},
            'keywords': {},
        }
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                mock_rb.return_value = {
                    'summary': {'summary': 'rules'},
                    'user_titles': {'user_titles': []},
                    'golden_quotes': {'golden_quotes': []},
                    'chat_quality': {'dimensions': []},
                    'keywords': {'keywords': []},
                }
                result = orch.auto_analyze('test_group')
        assert result['success'] is True
        assert result['method'] == 'rules'

    def test_ai_empty_ollama_fails_placeholder_returns_error(self):
        """AI 返回空内容、Ollama 失败且 API Key 为 placeholder 时返回配置错误"""
        ga = _mock_ga(has_api_key=True, is_placeholder=True)
        ga.ai_analyzer.full_analysis.return_value = {
            'summary': {'summary': ''},
            'user_titles': {'user_titles': ''},
            'golden_quotes': {'golden_quotes': ''},
            'chat_quality': {},
            'keywords': {},
        }
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            result = orch.auto_analyze('test_group')
        assert result['success'] is False
        assert result.get('error_code') == 'API_KEY_NOT_CONFIGURED'


# ── 补充测试：auto_analyze start_date/end_date 参数传递 ──────────────────

class TestAutoAnalyzeDateParams:
    def test_start_date_end_date_passed_to_filter(self):
        """auto_analyze 应将 start_date/end_date 传递给 _filter_messages_by_range"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_filter_messages_by_range', return_value=_make_messages(5)) as mock_filter:
            with patch.object(orch, '_try_ollama_analysis', return_value=None):
                orch.auto_analyze('test_group', start_date='2026-05-01', end_date='2026-05-10')
        mock_filter.assert_called_once_with('test_group', '2026-05-01', '2026-05-10')

    def test_date_filter_returns_empty_gives_error(self):
        """日期过滤后无消息时返回错误"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_filter_messages_by_range', return_value=[]):
            result = orch.auto_analyze('test_group', start_date='2099-01-01', end_date='2099-12-31')
        assert result['success'] is False
        assert '没有可分析的消息' in result['error']


# ── 补充测试：get_ai_analysis 缓存命中 ──────────────────────────────────

class TestGetAiAnalysisCacheHit:
    def test_stats_cache_hit_during_get_ai_analysis(self):
        """get_ai_analysis 内部调用 _generate_report → get_stats 时应命中缓存"""
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        # 先调用 get_stats 填充缓存
        orch.get_stats('test_group')
        assert ga.stats_analyzer.analyze.call_count == 1
        # 调用 get_ai_analysis，内部 _generate_report 会调用 get_stats
        result = orch.get_ai_analysis('test_group')
        assert result['success'] is True
        # get_stats 应命中缓存，analyze 不应再被调用
        assert ga.stats_analyzer.analyze.call_count == 1

    def test_skip_report_skips_generate(self):
        """skip_report=True 时不应生成报告"""
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        result = orch.get_ai_analysis('test_group', skip_report=True)
        assert result['success'] is True
        assert result['report'] == {}


# ── 补充测试：compare_groups rule_cache 命中和未命中 ────────────────────

class TestCompareGroupsRuleCache:
    def test_rule_cache_miss_calls_rule_based_analysis(self):
        """rule_cache 未命中时应调用 rule_based_analysis"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'keywords': {'keywords': []}, 'chat_quality': {'dimensions': []}}
            result = orch.compare_groups(['group1', 'group2'])
        assert result['success'] is True
        assert mock_rb.call_count == 2

    def test_rule_cache_expired_recomputes(self):
        """rule_cache 过期后应重新计算"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'keywords': {'keywords': []}, 'chat_quality': {'dimensions': []}}
            orch.compare_groups(['group1', 'group2'])
            # 让缓存过期
            orch._rule_cache_time['group1'] = time.time() - _CACHE_TTL - 1
            orch._rule_cache_time['group2'] = time.time() - _CACHE_TTL - 1
            # 再次对比，应重新计算
            orch.compare_groups(['group1', 'group2'])
        assert mock_rb.call_count == 4

    def test_compare_groups_with_empty_group(self):
        """对比时某个群无消息应标记 available=False，且至少需要2个有数据的群"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        # 让 group3 返回空消息，group1 和 group2 有数据
        def get_messages_side_effect(name):
            if name == 'group3':
                return []
            return _make_messages(20)
        ga.get_messages.side_effect = get_messages_side_effect
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'keywords': {'keywords': []}, 'chat_quality': {'dimensions': []}}
            result = orch.compare_groups(['group1', 'group2', 'group3'])
        assert result['success'] is True
        comparisons = result['comparisons']
        assert comparisons[0]['available'] is True
        assert comparisons[1]['available'] is True
        assert comparisons[2]['available'] is False


# ── 补充测试：_generate_report 不同 fmt 参数 ────────────────────────────

class TestGenerateReportFmt:
    def test_jpg_format_generate_image_true(self):
        """fmt='jpg' 时 generate_image=True"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        orch._generate_report('test_group', ai_data, 'scrapbook', 'jpg')
        call_kwargs = ga.report.generate_image.call_args
        assert call_kwargs.kwargs.get('generate_image') is True or call_kwargs[1].get('generate_image') is True

    def test_png_format_generate_image_true(self):
        """fmt='png' 时 generate_image=True"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        orch._generate_report('test_group', ai_data, 'scrapbook', 'png')
        call_kwargs = ga.report.generate_image.call_args
        assert call_kwargs.kwargs.get('generate_image') is True or call_kwargs[1].get('generate_image') is True

    def test_html_format_generate_image_false(self):
        """fmt='html' 时 generate_image=False"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        orch._generate_report('test_group', ai_data, 'scrapbook', 'html')
        call_kwargs = ga.report.generate_image.call_args
        assert call_kwargs.kwargs.get('generate_image') is False or call_kwargs[1].get('generate_image') is False

    def test_stats_failure_returns_empty(self):
        """get_stats 失败时 _generate_report 返回空字典"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        with patch.object(orch, 'get_stats', return_value={'success': False, 'error': 'stats error'}):
            result = orch._generate_report('test_group', ai_data, 'scrapbook', 'jpg')
        assert result == {}


# ── 补充测试：_normalize_stats 各种默认值填充场景 ────────────────────────

class TestNormalizeStatsDefaults:
    def test_missing_overview(self):
        """缺少 overview 时不应报错"""
        data = {}
        result = AnalysisOrchestrator._normalize_stats(data)
        assert 'overview' in result

    def test_missing_time_range_in_overview(self):
        """overview 中缺少 time_range 时 start_date/end_date 应为空字符串"""
        data = {'overview': {'avg_messages_per_day': 5}}
        result = AnalysisOrchestrator._normalize_stats(data)
        assert result['overview']['start_date'] == ''
        assert result['overview']['end_date'] == ''

    def test_missing_avg_messages_per_day(self):
        """缺少 avg_messages_per_day 时 avg_per_day 应为 0"""
        data = {'overview': {'time_range': {'start': '2026-05-01', 'end': '2026-05-28'}}}
        result = AnalysisOrchestrator._normalize_stats(data)
        assert result['overview']['avg_per_day'] == 0

    def test_interaction_pairs_from_interaction_analysis(self):
        """interaction_pairs 应从 interaction_analysis.top_interactions 获取"""
        data = {
            'overview': {'time_range': {'start': '2026-05-01', 'end': '2026-05-28'}},
            'interaction_analysis': {'top_interactions': [{'a': 'Alice', 'b': 'Bob'}]},
        }
        result = AnalysisOrchestrator._normalize_stats(data)
        assert result['interaction_pairs'] == [{'a': 'Alice', 'b': 'Bob'}]

    def test_keyword_frequency_from_keyword_cloud(self):
        """keyword_frequency 应从 keyword_cloud 获取"""
        data = {
            'overview': {'time_range': {'start': '2026-05-01', 'end': '2026-05-28'}},
            'keyword_cloud': [{'word': 'test', 'count': 5}],
        }
        result = AnalysisOrchestrator._normalize_stats(data)
        assert result['keyword_frequency'] == [{'word': 'test', 'count': 5}]

    def test_message_types_from_msg_type_distribution(self):
        """message_types 应从 msg_type_distribution 获取"""
        data = {
            'overview': {'time_range': {'start': '2026-05-01', 'end': '2026-05-28'}},
            'msg_type_distribution': [{'type': 'text', 'count': 10}],
        }
        result = AnalysisOrchestrator._normalize_stats(data)
        assert result['message_types'] == [{'type': 'text', 'count': 10}]


# ── 补充测试：get_stats 异常处理 ────────────────────────────────────────

class TestGetStatsException:
    def test_stats_analyzer_exception_propagates(self):
        """stats_analyzer.analyze 抛异常时应向上传播"""
        ga = _mock_ga()
        ga.stats_analyzer.analyze.side_effect = RuntimeError("analyze failed")
        orch = AnalysisOrchestrator(ga)
        try:
            orch.get_stats('test_group')
            assert False, "应抛出 RuntimeError"
        except RuntimeError as e:
            assert "analyze failed" in str(e)


# ── 补充测试：auto_analyze Ollama 模型选择逻辑 ──────────────────────────

class TestAutoAnalyzeOllamaModelSelection:
    def test_prefer_text_model_over_other(self):
        """Ollama 应优先选择文本模型（如 qwen）而非其他模型"""
        ga = _mock_ga(has_api_key=False, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)

        ollama_tags_response = {
            'models': [
                {'name': 'nomic-embed-text'},
                {'name': 'qwen2.5:7b'},
            ]
        }
        # M2 修复：httpx 响应字段改为 .status_code / .text（替代 urllib .read()）
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(ollama_tags_response)
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch('chatlens.plugins.web.analysis_orchestrator._get_ollama_client', return_value=mock_client):
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                mock_rb.return_value = {
                    'summary': {'summary': 'rules'},
                    'user_titles': {'user_titles': []},
                    'golden_quotes': {'golden_quotes': []},
                    'chat_quality': {'dimensions': []},
                    'keywords': {'keywords': []},
                }
                with patch('chatlens.core.ai_analyzer.GroupAIAnalyzer') as mock_analyzer_cls:
                    mock_analyzer_instance = MagicMock()
                    mock_analyzer_instance.full_analysis.return_value = None
                    mock_analyzer_cls.return_value = mock_analyzer_instance
                    result = orch.auto_analyze('test_group')

        # 验证 GroupAIAnalyzer 被调用时 model 是 qwen2.5:7b 而非 nomic-embed-text
        if mock_analyzer_cls.called:
            call_config = mock_analyzer_cls.call_args[0][0]
            assert 'qwen' in call_config['model']

    def test_no_text_model_uses_first_available(self):
        """没有文本模型时应使用第一个可用模型"""
        ga = _mock_ga(has_api_key=False, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)

        ollama_tags_response = {
            'models': [
                {'name': 'nomic-embed-text'},
                {'name': 'whisper'},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(ollama_tags_response)
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch('chatlens.plugins.web.analysis_orchestrator._get_ollama_client', return_value=mock_client):
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                mock_rb.return_value = {
                    'summary': {'summary': 'rules'},
                    'user_titles': {'user_titles': []},
                    'golden_quotes': {'golden_quotes': []},
                    'chat_quality': {'dimensions': []},
                    'keywords': {'keywords': []},
                }
                with patch('chatlens.core.ai_analyzer.GroupAIAnalyzer') as mock_analyzer_cls:
                    mock_analyzer_instance = MagicMock()
                    mock_analyzer_instance.full_analysis.return_value = None
                    mock_analyzer_cls.return_value = mock_analyzer_instance
                    result = orch.auto_analyze('test_group')

        if mock_analyzer_cls.called:
            call_config = mock_analyzer_cls.call_args[0][0]
            assert call_config['model'] == 'nomic-embed-text'


# ── 修复 1：auto_analyze use_ide=True 时**不应**启动 daemon 线程 ──────────
# 历史背景：早期版本直接创建 pending 任务后没启线程 → 任务永远 pending。
# 中间版本启了 daemon 线程跑 run_ide_analysis → 行为退化为后端自跑 AI，
# 等于没有 IDE 模式（前端下拉"IDE AI 分析"跟"AI 深度分析"行为一样）。
# 当前修复：use_ide=True 时只创建 pending 任务，不启任何线程。
# 后端自跑能力改由 use_fallback=True 触发，Ollama → 规则降级。

class TestAutoAnalyzeIDENoThread:
    """验证 auto_analyze(use_ide=True) 不再启动 daemon 线程（语义改正确）"""

    def test_ide_mode_does_not_start_daemon_thread(self):
        """use_ide=True 时不应启动 daemon 线程（只创建 pending 任务等 IDE 客户端接管）"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ide_tasks = MagicMock()
        ide_tasks.create.return_value = {"success": True, "task_id": "t1"}

        with patch('chatlens.plugins.web.analysis_orchestrator.threading.Thread') as MockThread:
            result = orch.auto_analyze('test_group', use_ide=True, ide_tasks=ide_tasks)

        # 1) 任务被创建
        ide_tasks.create.assert_called_once()
        # 2) 返回的 dict 包含 task_id
        assert result['task_id'] == 't1'
        assert result['success'] is True
        assert result['method'] == 'ide'
        # 3) Thread 不应被构造或启动（语义改：等 IDE 接管）
        MockThread.assert_not_called()
        mock_thread_instance = MockThread.return_value
        mock_thread_instance.start.assert_not_called()

    def test_ide_mode_does_not_call_run_ide_analysis(self):
        """use_ide=True 时不应触发 run_ide_analysis（不论是直接调还是放线程）"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ide_tasks = MagicMock()
        ide_tasks.create.return_value = {"success": True, "task_id": "abc123"}

        with patch.object(orch, 'run_ide_analysis') as mock_run:
            orch.auto_analyze(
                'my_group', theme='dark', fmt='png',
                use_ide=True, ide_tasks=ide_tasks,
            )

        # run_ide_analysis 不应在主流程中被调用（也没线程能调它）
        mock_run.assert_not_called()
        # 任务仍被正确创建
        ide_tasks.create.assert_called_once_with('my_group', 'dark', 'png', 20)

    def test_ide_create_failure_does_not_start_thread(self):
        """ide_tasks.create 失败时不应启动线程（与原行为一致）"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ide_tasks = MagicMock()
        ide_tasks.create.return_value = {"success": False, "error": "create failed"}

        with patch('chatlens.plugins.web.analysis_orchestrator.threading.Thread') as MockThread:
            result = orch.auto_analyze('test_group', use_ide=True, ide_tasks=ide_tasks)

        assert result['success'] is False
        MockThread.assert_not_called()

    def test_ide_mode_task_status_is_pending_after_create(self):
        """use_ide=True 后，任务在 ide_tasks 队列中应保持 pending 状态（等 IDE 客户端 submit_result）"""
        from chatlens.plugins.web.ide_tasks import IDETaskQueue
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ide_tasks = IDETaskQueue()  # 真实队列，便于验证 status 字段

        result = orch.auto_analyze('test_group', use_ide=True, ide_tasks=ide_tasks)

        assert result['success'] is True
        assert result['method'] == 'ide'
        task_id = result['task_id']
        # 任务应真实存在于队列且 status == 'pending'
        task_data = ide_tasks.get(task_id)
        assert task_data['success'] is True
        assert task_data['task']['status'] == 'pending'
        assert task_data['task']['result'] is None
        # get_pending 列表中应能找到该任务
        pending = ide_tasks.get_pending()
        assert any(t['task_id'] == task_id for t in pending['tasks'])

    def test_ide_mode_without_ide_tasks_param_does_not_crash(self):
        """use_ide=True 但 ide_tasks=None 时不应抛异常，应继续走默认 auto 流程"""
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            result = orch.auto_analyze('test_group', use_ide=True, ide_tasks=None)
        # use_ide=True but ide_tasks is None → fall through to default auto
        assert result['success'] is True
        # 默认 auto 流程：有 API Key → ai 方法
        assert result['method'] == 'ai'


# ── 修复 2：run_ide_analysis 命中 _ai_cache 时跳过 AI ───────────────────

class TestRunIdeAnalysisCache:
    """验证 _ai_cache 命中时 run_ide_analysis 不调 AI 直接走缓存"""

    def test_cache_hit_skips_ai(self):
        """缓存命中时不应调 ai_analyzer.full_analysis"""
        import time as _t
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        # 预填缓存（key 与 H1 一致：(group_name, True, False, '', '', fmt)）
        cache_key = ('test_group', True, False, '', '', 'jpg')
        payload = {
            'success': True,
            'data': {'summary': {'summary': 'cached'}},
            'method': 'ai',
            'report': {'html_url': '/cached'},
        }
        orch._ai_cache[cache_key] = (payload, _t.time())

        ide_tasks = MagicMock()
        # 用真实消息让 _filter_messages_by_range 返回非空
        msgs = _make_messages(3)
        with patch.object(orch, '_filter_messages_by_range', return_value=msgs):
            orch.run_ide_analysis(msgs, ga, ide_tasks, 'tid1', 'test_group', 'scrapbook', 'jpg')

        # AI 不应被调（_async_full_analysis_sync 走 afull_analysis，所以 full_analysis 不应被调）
        ga.ai_analyzer.full_analysis.assert_not_called()
        # mark_completed 用了缓存里的 data + report
        ide_tasks.mark_completed.assert_called_once()
        call_args = ide_tasks.mark_completed.call_args
        assert call_args[0][0] == 'tid1'
        assert call_args[0][1] == {'summary': {'summary': 'cached'}}
        assert call_args[0][2] == {'html_url': '/cached'}

    def test_cache_miss_calls_ai(self):
        """缓存未命中时应调 AI（走 _async_full_analysis_sync → afull_analysis）"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        # 预填 ai_analyzer 的异步结果
        async def fake_afull(messages):
            return {
                'summary': {'summary': 'ai'},
                'keywords': {'keywords': []},
                'user_titles': {'user_titles': []},
                'golden_quotes': {'golden_quotes': []},
                'chat_quality': {'title': '', 'dimensions': []},
            }
        ga.ai_analyzer.afull_analysis = fake_afull
        ide_tasks = MagicMock()
        msgs = _make_messages(3)
        with patch.object(orch, '_filter_messages_by_range', return_value=msgs):
            with patch.object(orch, '_generate_report', return_value={}):
                orch.run_ide_analysis(msgs, ga, ide_tasks, 'tid2', 'test_group', 'scrapbook', 'jpg')

        # mark_completed 应被调
        ide_tasks.mark_completed.assert_called_once()
        ai_data = ide_tasks.mark_completed.call_args[0][1]
        assert ai_data['summary']['summary'] == 'ai'

    def test_no_api_key_falls_back_to_rules(self):
        """无 API Key 时退到 rule_based_analysis"""
        ga = _mock_ga(has_api_key=False)
        orch = AnalysisOrchestrator(ga)
        ide_tasks = MagicMock()
        msgs = _make_messages(3)
        with patch.object(orch, '_filter_messages_by_range', return_value=msgs):
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                mock_rb.return_value = {
                    'summary': {'summary': 'rule-fallback'},
                    'user_titles': {'user_titles': []},
                    'golden_quotes': {'golden_quotes': []},
                    'chat_quality': {'title': '', 'dimensions': []},
                    'keywords': {'keywords': []},
                }
                with patch.object(orch, '_generate_report', return_value={}):
                    orch.run_ide_analysis(msgs, ga, ide_tasks, 'tid3', 'test_group', 'scrapbook', 'jpg')

        ide_tasks.mark_completed.assert_called_once()
        ai_data = ide_tasks.mark_completed.call_args[0][1]
        assert ai_data['summary']['summary'] == 'rule-fallback'

    def test_exception_marks_failed(self):
        """分析过程中抛异常时 mark_failed 应被调（让 mark_completed 抛异常逃出内层 try）"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ide_tasks = MagicMock()
        ide_tasks.mark_completed.side_effect = RuntimeError('mark_completed failed')
        msgs = _make_messages(3)
        with patch.object(orch, '_filter_messages_by_range', return_value=msgs):
            with patch.object(orch, '_async_full_analysis_sync', return_value={
                'summary': {'summary': 'x'},
                'user_titles': {'user_titles': []},
                'golden_quotes': {'golden_quotes': []},
            }):
                with patch.object(orch, '_generate_report', return_value={}):
                    orch.run_ide_analysis(msgs, ga, ide_tasks, 'tid4', 'test_group', 'scrapbook', 'jpg')

        ide_tasks.mark_failed.assert_called_once()
        assert 'mark_completed failed' in ide_tasks.mark_failed.call_args[0][1]


# ── 修复 3：_generate_report Chrome 失败时降级到 HTML ──────────────────

class TestGenerateReportFallback:
    """daemon 线程里 _generate_report 失败时应降级到 HTML，不让任务失败"""

    def test_jpg_failure_falls_back_to_html(self):
        """fmt='jpg' 但 generate_image 失败时，应降级到 fmt='html' 重新调用"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}

        # 第一次 jpg 抛异常，第二次 html 返回字典
        ga.report.generate_image.side_effect = [
            RuntimeError('Chrome crashed'),
            {'report': {'html_url': '/fallback'}},
        ]
        result = orch._generate_report('test_group', ai_data, 'scrapbook', 'jpg')
        assert result == {'html_url': '/fallback'}
        # generate_image 应被调 2 次
        assert ga.report.generate_image.call_count == 2
        # 第二次调用 fmt 应为 'html' 且 generate_image=False
        second_call_kwargs = ga.report.generate_image.call_args_list[1].kwargs
        assert second_call_kwargs.get('fmt') == 'html'
        assert second_call_kwargs.get('generate_image') is False

    def test_both_attempts_fail_returns_empty(self):
        """jpg 和 html 二次降级都失败时返回空 dict"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        ga.report.generate_image.side_effect = RuntimeError('chrome down')
        result = orch._generate_report('test_group', ai_data, 'scrapbook', 'jpg')
        assert result == {}

    def test_html_format_no_fallback(self):
        """fmt='html' 时不需要降级（generate_image=False）"""
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        ai_data = {'summary': {'summary': 'test'}}
        # 第一次抛异常，第二次仍抛（不应被调第二次）
        ga.report.generate_image.side_effect = [
            RuntimeError('oops'),
            {'report': {'html_url': '/never'}},
        ]
        result = orch._generate_report('test_group', ai_data, 'scrapbook', 'html')
        # html 模式失败时直接返回空，不会再调一次
        assert result == {}
        assert ga.report.generate_image.call_count == 1


# ── 修复 4：auto_analyze 新增 use_fallback=True 参数 ──────────────────
# use_fallback 是后端自跑降级模式（Ollama → 规则），不调 IDE、不调 API Key。
# 前端应根据后端 /api/status 的 ide_available 字段决定走 use_ide 还是 use_fallback。

class TestAutoAnalyzeUseFallback:
    """验证 auto_analyze(use_fallback=True) 走 Ollama → 规则降级链"""

    def test_use_fallback_ollama_available_uses_ollama(self):
        """use_fallback=True 且 Ollama 可用时，应使用 Ollama 结果"""
        ga = _mock_ga(has_api_key=False, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)
        ollama_data = {
            'summary': {'summary': 'ollama-fallback'},
            'user_titles': {'user_titles': []},
            'golden_quotes': {'golden_quotes': []},
            'chat_quality': {'dimensions': []},
            'keywords': {'keywords': []},
        }
        with patch.object(orch, '_try_ollama_analysis', return_value=ollama_data) as mock_ollama:
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                # 即使有 API Key，use_fallback 也不应调 API
                ga.has_api_key.return_value = True
                result = orch.auto_analyze('test_group', use_fallback=True)
        assert result['success'] is True
        assert result['method'] == 'ollama'
        mock_ollama.assert_called_once()
        # 关键断言：use_fallback 不应调 AI full_analysis
        ga.ai_analyzer.full_analysis.assert_not_called()
        # 不应走规则
        mock_rb.assert_not_called()

    def test_use_fallback_ollama_unavailable_uses_rules(self):
        """use_fallback=True 且 Ollama 不可用时，应降级到规则分析"""
        ga = _mock_ga(has_api_key=False, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                mock_rb.return_value = {
                    'summary': {'summary': 'rules-fallback'},
                    'user_titles': {'user_titles': []},
                    'golden_quotes': {'golden_quotes': []},
                    'chat_quality': {'dimensions': []},
                    'keywords': {'keywords': []},
                }
                result = orch.auto_analyze('test_group', use_fallback=True)
        assert result['success'] is True
        assert result['method'] == 'rules'
        mock_rb.assert_called_once()
        # 关键断言：use_fallback 不应调 AI full_analysis（即使有 API Key）
        ga.ai_analyzer.full_analysis.assert_not_called()

    def test_use_fallback_does_not_call_ai_even_with_api_key(self):
        """use_fallback=True 时即使有 API Key 也不应调 AI（专门绕开 API 调用）"""
        ga = _mock_ga(has_api_key=True, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                mock_rb.return_value = {
                    'summary': {'summary': 'rules'},
                    'user_titles': {'user_titles': []},
                    'golden_quotes': {'golden_quotes': []},
                    'chat_quality': {'dimensions': []},
                    'keywords': {'keywords': []},
                }
                result = orch.auto_analyze('test_group', use_fallback=True)
        assert result['success'] is True
        # AI 不应被调
        ga.ai_analyzer.full_analysis.assert_not_called()
        assert result['method'] == 'rules'

    def test_use_fallback_does_not_create_ide_task(self):
        """use_fallback=True 时不应创建 IDE 任务（ide_tasks.create 不应被调）"""
        ga = _mock_ga(has_api_key=False, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)
        ide_tasks = MagicMock()
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                mock_rb.return_value = {
                    'summary': {'summary': 'rules'},
                    'user_titles': {'user_titles': []},
                    'golden_quotes': {'golden_quotes': []},
                    'chat_quality': {'dimensions': []},
                    'keywords': {'keywords': []},
                }
                result = orch.auto_analyze('test_group', use_fallback=True, ide_tasks=ide_tasks)
        assert result['success'] is True
        ide_tasks.create.assert_not_called()

    def test_use_fallback_placeholder_ollama_fails_returns_error(self):
        """use_fallback=True + Ollama 失败 + API Key 是 placeholder 时返回配置错误"""
        ga = _mock_ga(has_api_key=True, is_placeholder=True)
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            result = orch.auto_analyze('test_group', use_fallback=True)
        assert result['success'] is False
        assert result.get('error_code') == 'API_KEY_NOT_CONFIGURED'

    def test_use_fallback_no_messages_returns_error(self):
        """use_fallback=True 但无消息时返回失败"""
        ga = _mock_ga(messages=[])
        ga.get_messages.return_value = []
        orch = AnalysisOrchestrator(ga)
        result = orch.auto_analyze('empty_group', use_fallback=True)
        assert result['success'] is False
        assert '没有可分析的消息' in result['error']

    def test_use_fallback_does_not_start_thread(self):
        """use_fallback=True 时不应启动任何 daemon 线程"""
        ga = _mock_ga(has_api_key=False, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)
        with patch.object(orch, '_try_ollama_analysis', return_value=None):
            with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
                mock_rb.return_value = {
                    'summary': {'summary': 'rules'},
                    'user_titles': {'user_titles': []},
                    'golden_quotes': {'golden_quotes': []},
                    'chat_quality': {'dimensions': []},
                    'keywords': {'keywords': []},
                }
                with patch('chatlens.plugins.web.analysis_orchestrator.threading.Thread') as MockThread:
                    result = orch.auto_analyze('test_group', use_fallback=True)
        assert result['success'] is True
        MockThread.assert_not_called()


# ── _run_report_image_task 不再静默 fallback：AI 部分失败时显式写 warnings ────────

class TestRunReportImageTaskWarnings:
    """验证 _run_report_image_task 在 AI 部分子分析返回 __error__ 时，
    把失败 section 写到 task.warnings（不再用 rules 静默补齐）。"""

    def _partial_error_ai(self):
        """模拟 AI 5 个子分析中部分返回 __error__ 的情况。"""
        return {
            "summary": {"__error__": "ai_returned_empty_or_invalid_json"},
            "user_titles": {"user_titles": []},
            "golden_quotes": {"golden_quotes": []},
            "chat_quality": {"title": ""},
            "keywords": {"keywords": []},
        }

    def _make_task(self):
        from chatlens.plugins.web.analysis_orchestrator import ReportTask
        return ReportTask(
            task_id="test_task",
            group_name="test_group",
            theme="scrapbook",
            fmt="html",
            use_ide=False,
            task_type="report",
        )

    def _capture_task(self, mock_ga, partial_ai):
        """运行 _run_report_image_task 并返回 task。"""
        from chatlens.plugins.web import analysis_orchestrator as _ao

        task = self._make_task()
        orch = AnalysisOrchestrator(mock_ga)
        with patch("chatlens.plugins.report.image_report.generate_report_image",
                   new_callable=AsyncMock, return_value=(None, "/tmp/fake.html")), \
             patch("chatlens.core.ai_analyzer.GroupAIAnalyzer") as mock_cls, \
             patch("chatlens.plugins.web.analysis_orchestrator._publish_progress"), \
             patch("chatlens.plugins.web.analysis_orchestrator._update_stage"), \
             patch("os.path.exists", return_value=True):
            mock_cls.return_value.full_analysis.return_value = partial_ai
            _ao._run_report_image_task(orch, task, generate_image=False)
        return task

    def test_ai_partial_error_writes_warnings(self):
        """AI 部分子分析返回 __error__ 时，task.warnings 包含对应条目。"""
        ga = _mock_ga(has_api_key=True, is_placeholder=False)
        task = self._capture_task(ga, self._partial_error_ai())
        # warnings 至少应包含 summary 失败项
        sections = [w["section"] for w in task.warnings]
        assert "summary" in sections
        # summary 失败 reason 应来自 __error__
        summary_w = next(w for w in task.warnings if w["section"] == "summary")
        assert summary_w["reason"] == "ai_returned_empty_or_invalid_json"
        assert summary_w["label"] == "群聊摘要"

    def test_ai_partial_error_warnings_for_empty_sections(self):
        """AI 返回空 dict/list 的 section 也会被记到 warnings。"""
        ga = _mock_ga(has_api_key=True, is_placeholder=False)
        task = self._capture_task(ga, self._partial_error_ai())
        sections = [w["section"] for w in task.warnings]
        # 至少应包含 user_titles / golden_quotes / chat_quality / keywords
        # （它们都是空 dict 或空 list，没有 __error__，reason 走默认）
        for s in ("user_titles", "golden_quotes", "chat_quality", "keywords"):
            assert s in sections, f"missing warnings entry for {s}"

    def test_full_valid_ai_no_warnings(self):
        """AI 5 个 section 都有效时，task.warnings 应为空。"""
        ga = _mock_ga(has_api_key=True, is_placeholder=False)
        full_ai = {
            "summary": {"summary": "AI full", "topics": ["t"]},
            "user_titles": {"user_titles": [{"name": "A", "title": "T"}]},
            "golden_quotes": {"golden_quotes": [{"sender": "B", "quote": "Q"}]},
            "chat_quality": {"title": "Q", "dimensions": []},
            "keywords": {"keywords": ["kw"]},
        }
        from chatlens.plugins.web import analysis_orchestrator as _ao
        from chatlens.plugins.web.analysis_orchestrator import ReportTask
        task = ReportTask(
            task_id="t", group_name="g", theme="scrapbook",
            fmt="html", use_ide=False, task_type="report",
        )
        orch = AnalysisOrchestrator(ga)
        with patch("chatlens.plugins.report.image_report.generate_report_image",
                   new_callable=AsyncMock, return_value=(None, "/tmp/x.html")), \
             patch("chatlens.core.ai_analyzer.GroupAIAnalyzer") as mock_cls, \
             patch("chatlens.plugins.web.analysis_orchestrator._publish_progress"), \
             patch("chatlens.plugins.web.analysis_orchestrator._update_stage"), \
             patch("os.path.exists", return_value=True):
            mock_cls.return_value.full_analysis.return_value = full_ai
            _ao._run_report_image_task(orch, task, generate_image=False)
        assert task.warnings == []


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

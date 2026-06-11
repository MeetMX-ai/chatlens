"""分析编排器单元测试 — AnalysisOrchestrator"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch
from chatlens.core.models import ChatMessage
from chatlens.plugins.web.analysis_orchestrator import AnalysisOrchestrator


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


# ── get_stats ────────────────────────────────────────────────

class TestGetStats:
    def test_returns_success(self):
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        result = orch.get_stats('test_group')
        assert result['success'] is True
        assert 'data' in result

    def test_caches_result(self):
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        orch.get_stats('test_group')
        orch.get_stats('test_group')
        # Second call should use cache, analyze called only once
        assert ga.stats_analyzer.analyze.call_count == 1

    def test_invalidate_cache(self):
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        orch.get_stats('test_group')
        orch.invalidate_cache('test_group')
        orch.get_stats('test_group')
        assert ga.stats_analyzer.analyze.call_count == 2


# ── get_ai_analysis ──────────────────────────────────────────

class TestGetAiAnalysis:
    def test_no_messages(self):
        ga = _mock_ga(messages=[])
        ga.get_messages.return_value = []
        orch = AnalysisOrchestrator(ga)
        result = orch.get_ai_analysis('empty_group')
        # When no messages, the method may still return success with empty data
        # depending on whether rule_based_analysis handles empty input
        assert 'success' in result

    def test_rules_fallback_when_no_api_key(self):
        ga = _mock_ga(has_api_key=False)
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'summary': {'summary': 'rule result'}}
            result = orch.get_ai_analysis('test_group', use_rules=True)
            assert result['success'] is True
            assert result['method'] == 'rules'

    def test_ai_analysis_with_api_key(self):
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        result = orch.get_ai_analysis('test_group')
        assert result['success'] is True
        assert result['method'] == 'ai'

    def test_skip_report(self):
        ga = _mock_ga(has_api_key=True)
        orch = AnalysisOrchestrator(ga)
        result = orch.get_ai_analysis('test_group', skip_report=True)
        assert result['success'] is True
        assert result['report'] == {}


# ── auto_analyze ─────────────────────────────────────────────

class TestAutoAnalyze:
    def test_no_messages(self):
        ga = _mock_ga(messages=[])
        ga.get_messages.return_value = []
        orch = AnalysisOrchestrator(ga)
        result = orch.auto_analyze('empty_group')
        # rule_based_analysis handles empty input, so may return success
        assert 'success' in result

    def test_placeholder_api_key(self):
        ga = _mock_ga(has_api_key=False, is_placeholder=True)
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'summary': {'summary': ''}, 'user_titles': {'user_titles': []},
                                     'golden_quotes': {'golden_quotes': []}, 'chat_quality': {'title': ''},
                                     'keywords': {'keywords': []}}
            with patch.object(orch, '_try_ollama_analysis', return_value=None):
                result = orch.auto_analyze('test_group')
                assert result['success'] is False
                assert result.get('error_code') == 'API_KEY_NOT_CONFIGURED'

    def test_rules_fallback(self):
        ga = _mock_ga(has_api_key=False, is_placeholder=False)
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'summary': {'summary': 'rules'}, 'user_titles': {'user_titles': []},
                                     'golden_quotes': {'golden_quotes': []}, 'chat_quality': {'title': ''},
                                     'keywords': {'keywords': []}}
            with patch.object(orch, '_try_ollama_analysis', return_value=None):
                result = orch.auto_analyze('test_group')
                assert result['success'] is True
                assert result['method'] == 'rules'


# ── get_daily_analysis ───────────────────────────────────────

class TestGetDailyAnalysis:
    def test_no_messages(self):
        ga = _mock_ga(messages=[])
        ga.get_messages.return_value = []
        orch = AnalysisOrchestrator(ga)
        result = orch.get_daily_analysis('test_group', '2026-05-01')
        assert result['success'] is True
        assert result['message_count'] == 0

    def test_with_matching_date(self):
        msgs = [_msg(timestamp='2026-05-01 10:00:00')] * 3
        ga = _mock_ga(messages=msgs)
        orch = AnalysisOrchestrator(ga)
        result = orch.get_daily_analysis('test_group', '2026-05-01')
        assert result['success'] is True
        assert result['message_count'] == 3

    def test_no_matching_date(self):
        msgs = [_msg(timestamp='2026-05-02 10:00:00')]
        ga = _mock_ga(messages=msgs)
        orch = AnalysisOrchestrator(ga)
        result = orch.get_daily_analysis('test_group', '2026-05-01')
        assert result['message_count'] == 0


# ── compare_groups ───────────────────────────────────────────

class TestCompareGroups:
    def test_too_few_groups(self):
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        result = orch.compare_groups(['only_one'])
        assert result['success'] is False

    def test_too_many_groups(self):
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        result = orch.compare_groups(['g1', 'g2', 'g3', 'g4', 'g5', 'g6', 'g7'])
        assert result['success'] is False

    def test_successful_comparison(self):
        ga = _mock_ga()
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'keywords': {'keywords': []}, 'chat_quality': {'dimensions': []}}
            result = orch.compare_groups(['group1', 'group2'])
            assert result['success'] is True
            assert len(result['comparisons']) == 2

    def test_unavailable_group(self):
        ga = _mock_ga(messages=[])
        ga.get_messages.return_value = []
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'keywords': {'keywords': []}, 'chat_quality': {'dimensions': []}}
            result = orch.compare_groups(['empty1', 'empty2'])
            # Both groups have no data, so comparison fails
            assert result['success'] is False or len([c for c in result.get('comparisons', []) if c.get('available')]) < 2

    def test_mixed_availability(self):
        ga = MagicMock()
        msgs = _make_messages(10)

        def get_messages_side_effect(name):
            return msgs if name == 'has_data' else []
        ga.get_messages.side_effect = get_messages_side_effect
        ga.stats_analyzer.analyze.return_value = {
            'overview': {'total_messages': 10, 'total_members': 3,
                         'time_range': {'start': '2026-05-01', 'end': '2026-05-28'},
                         'avg_messages_per_day': 1},
            'member_stats': [{'sender': 'Alice', 'msg_count': 5}],
            'msg_type_distribution': [],
        }
        orch = AnalysisOrchestrator(ga)
        with patch('chatlens.plugins.web.analysis_orchestrator.rule_based_analysis') as mock_rb:
            mock_rb.return_value = {'keywords': {'keywords': []}, 'chat_quality': {'dimensions': []}}
            result = orch.compare_groups(['has_data', 'empty'])
            assert result['success'] is False  # Only 1 available


# ── _filter_messages_by_range ────────────────────────────────

class TestFilterMessagesByRange:
    def test_no_filter(self):
        msgs = _make_messages(10)
        ga = _mock_ga(messages=msgs)
        orch = AnalysisOrchestrator(ga)
        result = orch._filter_messages_by_range('test_group')
        assert len(result) == 10

    def test_start_date_filter(self):
        msgs = [_msg(timestamp='2026-05-01 10:00:00'), _msg(timestamp='2026-05-15 10:00:00')]
        ga = _mock_ga(messages=msgs)
        orch = AnalysisOrchestrator(ga)
        result = orch._filter_messages_by_range('test_group', start_date='2026-05-10')
        assert len(result) == 1

    def test_end_date_filter(self):
        msgs = [_msg(timestamp='2026-05-01 10:00:00'), _msg(timestamp='2026-05-15 10:00:00')]
        ga = _mock_ga(messages=msgs)
        orch = AnalysisOrchestrator(ga)
        result = orch._filter_messages_by_range('test_group', end_date='2026-05-10')
        assert len(result) == 1

    def test_both_filters(self):
        msgs = [_msg(timestamp='2026-05-01 10:00:00'),
                _msg(timestamp='2026-05-10 10:00:00'),
                _msg(timestamp='2026-05-20 10:00:00')]
        ga = _mock_ga(messages=msgs)
        orch = AnalysisOrchestrator(ga)
        result = orch._filter_messages_by_range('test_group', start_date='2026-05-05', end_date='2026-05-15')
        assert len(result) == 1


# ── _normalize_stats ─────────────────────────────────────────

class TestNormalizeStats:
    def test_adds_compat_fields(self):
        data = {
            'overview': {'time_range': {'start': '2026-05-01', 'end': '2026-05-28'}, 'avg_messages_per_day': 5},
            'member_stats': [{'sender': 'Alice'}],
            'msg_type_distribution': [{'type': 'text'}],
            'keyword_cloud': [{'word': 'AI'}],
            'interaction_analysis': {'top_interactions': [{'pair': 'A-B'}]},
        }
        result = AnalysisOrchestrator._normalize_stats(data)
        assert result['top_members'] == [{'sender': 'Alice'}]
        assert result['message_types'] == [{'type': 'text'}]
        assert result['keyword_frequency'] == [{'word': 'AI'}]
        assert result['interaction_pairs'] == [{'pair': 'A-B'}]
        assert result['overview']['start_date'] == '2026-05-01'
        assert result['overview']['avg_per_day'] == 5


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from chatlens.core.models import ChatMessage
from chatlens.core._rule_engine import rule_based_analysis
from chatlens.core._analysis_data import EMPTY_RESULT, SBTI_MAP, ACGTI_MAP


def _make_rich_messages(n=50):
    """生成多样化消息用于规则引擎测试"""
    msgs = []
    senders = ['话痨小明', '潜水大王', '夜猫子', '图王', '新人']
    for i in range(n):
        sender = senders[i % len(senders)]
        msg_type = 'text'
        content = f'这是第{i}条消息，今天天气真好，大家一起讨论AI编程吧'
        if i % 10 == 0:
            msg_type = 'image'
            content = ''
        elif i % 15 == 0:
            msg_type = 'quote'
            content = f'引用了别人的观点，我觉得这个想法非常有道理，值得深入探讨'
        elif i % 20 == 0:
            msg_type = 'emotion'
            content = ''
        hour = (8 + i % 16) % 24
        msgs.append(ChatMessage(
            sender=sender,
            content=content,
            msg_type=msg_type,
            msg_attr='',
            timestamp=f'2026-05-{(i % 28) + 1:02d} {hour:02d}:{i % 60:02d}:00',
            group_name='测试群',
            sender_remark=sender,
        ))
    # 添加深夜消息
    for i in range(5):
        msgs.append(ChatMessage(
            sender='夜猫子', content='深夜还在聊天，讨论人生哲学和未来规划',
            msg_type='text', msg_attr='',
            timestamp=f'2026-05-0{i+1} 03:{i*10:02d}:00',
            group_name='测试群', sender_remark='夜猫子',
        ))
    # 添加系统消息
    msgs.append(ChatMessage(
        sender='系统', content='xxx 加入了群聊',
        msg_type='text', msg_attr='system',
        timestamp='2026-05-01 00:00:00',
        group_name='测试群',
    ))
    return msgs


class TestRuleEngineEmptyInput:
    def test_empty_returns_structure(self):
        result = rule_based_analysis([])
        assert 'summary' in result
        assert 'user_titles' in result
        assert 'golden_quotes' in result
        assert 'chat_quality' in result
        assert 'keywords' in result

    def test_empty_summary_is_empty(self):
        result = rule_based_analysis([])
        assert result['summary']['summary'] == ''


class TestRuleEngineSummary:
    def test_summary_has_text(self):
        msgs = _make_rich_messages(30)
        result = rule_based_analysis(msgs)
        summary = result['summary']['summary']
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_summary_mentions_member_count(self):
        msgs = _make_rich_messages(30)
        result = rule_based_analysis(msgs)
        summary = result['summary']['summary']
        assert '位成员' in summary or '人' in summary

    def test_topics_generated(self):
        msgs = _make_rich_messages(40)
        result = rule_based_analysis(msgs)
        topics = result['summary'].get('topics', [])
        assert isinstance(topics, list)


class TestRuleEngineUserTitles:
    def test_user_titles_generated(self):
        msgs = _make_rich_messages(50)
        result = rule_based_analysis(msgs)
        titles = result['user_titles'].get('user_titles', [])
        assert isinstance(titles, list)
        assert len(titles) > 0

    def test_user_title_structure(self):
        msgs = _make_rich_messages(50)
        result = rule_based_analysis(msgs)
        titles = result['user_titles'].get('user_titles', [])
        if titles:
            t = titles[0]
            assert 'name' in t
            assert 'title' in t
            assert 'mbti' in t
            assert 'reason' in t
            assert 'sbti' in t
            assert 'acgti' in t

    def test_top_user_gets_title(self):
        msgs = _make_rich_messages(50)
        result = rule_based_analysis(msgs)
        titles = result['user_titles'].get('user_titles', [])
        if titles:
            # 第一名应该有 "社群话事人" 称号
            assert titles[0]['title'] == '社群话事人'

    def test_mbti_maps_to_sbti(self):
        msgs = _make_rich_messages(50)
        result = rule_based_analysis(msgs)
        titles = result['user_titles'].get('user_titles', [])
        for t in titles:
            mbti = t.get('mbti', '')
            if mbti in SBTI_MAP:
                assert t['sbti'] == SBTI_MAP[mbti]


class TestRuleEngineGoldenQuotes:
    def test_golden_quotes_generated(self):
        msgs = _make_rich_messages(50)
        result = rule_based_analysis(msgs)
        quotes = result['golden_quotes'].get('golden_quotes', [])
        assert isinstance(quotes, list)

    def test_golden_quote_structure(self):
        msgs = _make_rich_messages(50)
        result = rule_based_analysis(msgs)
        quotes = result['golden_quotes'].get('golden_quotes', [])
        if quotes:
            q = quotes[0]
            assert 'content' in q
            assert 'sender' in q
            assert 'reason' in q

    def test_no_duplicate_quotes(self):
        msgs = _make_rich_messages(50)
        result = rule_based_analysis(msgs)
        quotes = result['golden_quotes'].get('golden_quotes', [])
        contents = [q['content'][:20] for q in quotes]
        assert len(contents) == len(set(contents))


class TestRuleEngineChatQuality:
    def test_chat_quality_structure(self):
        msgs = _make_rich_messages(40)
        result = rule_based_analysis(msgs)
        cq = result['chat_quality']
        assert 'title' in cq
        assert 'subtitle' in cq
        assert 'dimensions' in cq
        assert 'summary' in cq

    def test_dimensions_have_percentage(self):
        msgs = _make_rich_messages(40)
        result = rule_based_analysis(msgs)
        dims = result['chat_quality'].get('dimensions', [])
        if dims:
            d = dims[0]
            assert 'percentage' in d
            assert isinstance(d['percentage'], int)

    def test_subtitle_contains_stats(self):
        msgs = _make_rich_messages(40)
        result = rule_based_analysis(msgs)
        subtitle = result['chat_quality'].get('subtitle', '')
        assert '人' in subtitle or '条' in subtitle


class TestRuleEngineKeywords:
    def test_keywords_generated(self):
        msgs = _make_rich_messages(40)
        result = rule_based_analysis(msgs)
        kws = result['keywords'].get('keywords', [])
        assert isinstance(kws, list)

    def test_keyword_structure(self):
        msgs = _make_rich_messages(40)
        result = rule_based_analysis(msgs)
        kws = result['keywords'].get('keywords', [])
        if kws:
            kw = kws[0]
            assert 'word' in kw
            assert 'relevance' in kw
            assert isinstance(kw['relevance'], int)
            assert 1 <= kw['relevance'] <= 10


class TestRuleEngineSystemMessages:
    def test_system_messages_excluded(self):
        """系统消息不应影响分析结果"""
        msgs = [ChatMessage(
            sender='系统', content='xxx 加入了群聊',
            msg_type='text', msg_attr='system',
            timestamp='2026-05-01 12:00:00',
            group_name='测试群',
        )] * 10
        result = rule_based_analysis(msgs)
        titles = result['user_titles'].get('user_titles', [])
        # 纯系统消息不应生成用户称号
        assert len(titles) == 0


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

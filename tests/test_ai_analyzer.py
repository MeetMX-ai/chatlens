"""AI 分析器单元测试 — ai_analyzer.py 的 _do_* 函数与 GroupAIAnalyzer 重试/分析方法"""

import asyncio
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from chatlens.core.models import ChatMessage
from chatlens.core.ai_analyzer import (
    _do_summary, _do_keywords, _do_user_titles, _do_golden_quotes, _do_chat_quality,
    GroupAIAnalyzer,
)


def _msg(sender='Alice', content='Hello world', msg_type='text', msg_attr='',
         timestamp='2026-05-01 10:00:00', group_name='TestGroup', sender_remark=''):
    return ChatMessage(sender=sender, content=content, msg_type=msg_type,
                       msg_attr=msg_attr, timestamp=timestamp,
                       group_name=group_name, sender_remark=sender_remark)


def _make_messages(n=20):
    return [_msg(sender=f'User{i%5}', content=f'Message {i} about AI and coding',
                 timestamp=f'2026-05-{(i%28)+1:02d} {8+i%14:02d}:{i%60:02d}:00')
            for i in range(n)]


# ── _do_* 函数使用 mock call_fn ─────────────────────────────

class TestDoSummary:
    def test_empty_messages(self):
        result = _do_summary(lambda *a, **kw: None, [])
        # 无消息：整段视为 AI 失败，返回 __error__ 标记
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"

    def test_with_mock_call_fn(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            parsed = {'summary': 'Test summary', 'topics': [], 'key_points': [], 'action_items': []}
            return postprocess(parsed) if postprocess else parsed
        msgs = _make_messages(10)
        result = _do_summary(mock_call_fn, msgs)
        assert result['summary'] == 'Test summary'

    def test_call_fn_receives_prompts(self):
        received = {}
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            received['system'] = system
            received['user'] = user
            parsed = {'summary': 'ok', 'topics': [], 'key_points': [], 'action_items': []}
            return postprocess(parsed) if postprocess else parsed
        _do_summary(mock_call_fn, _make_messages(5))
        assert '群聊' in received['system'] or 'JSON' in received['system']
        assert '消息' in received['user']

    def test_ai_returns_none_marks_error(self):
        """AI 返回 None 时，postprocess 应返回 __error__ 标记。"""
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            return postprocess(None)
        result = _do_summary(mock_call_fn, _make_messages(10))
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"


class TestDoKeywords:
    def test_empty_messages(self):
        result = _do_keywords(lambda *a, **kw: None, [])
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"

    def test_with_mock_call_fn(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            parsed = {'keywords': [{'word': 'AI', 'relevance': 8}], 'hot_topics': []}
            return postprocess(parsed) if postprocess else parsed
        result = _do_keywords(mock_call_fn, _make_messages(10))
        assert len(result['keywords']) == 1

    def test_ai_returns_none_marks_error(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            return postprocess(None)
        result = _do_keywords(mock_call_fn, _make_messages(10))
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"


class TestDoUserTitles:
    def test_empty_messages(self):
        result = _do_user_titles(lambda *a, **kw: None, [])
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"

    def test_with_mock_call_fn(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            parsed = {'user_titles': [{'name': 'U1', 'title': '话痨', 'mbti': 'ENFP'}]}
            return postprocess(parsed) if postprocess else parsed
        result = _do_user_titles(mock_call_fn, _make_messages(20))
        assert len(result['user_titles']) == 1

    def test_sbti_acgti_added(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            parsed = {'user_titles': [{'name': 'U1', 'title': '话痨', 'mbti': 'ENFP'}]}
            return postprocess(parsed) if postprocess else parsed
        result = _do_user_titles(mock_call_fn, _make_messages(20))
        t = result['user_titles'][0]
        assert 'sbti' in t
        assert 'acgti' in t

    def test_ai_returns_none_marks_error(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            return postprocess(None)
        result = _do_user_titles(mock_call_fn, _make_messages(20))
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"


class TestDoGoldenQuotes:
    def test_empty_messages(self):
        result = _do_golden_quotes(lambda *a, **kw: None, [])
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"

    def test_no_text_messages(self):
        msgs = [_msg(msg_type='image')] * 5
        result = _do_golden_quotes(lambda *a, **kw: None, msgs)
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"

    def test_with_mock_call_fn(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            parsed = {'golden_quotes': [{'content': 'Great insight', 'sender': 'U1', 'reason': 'deep'}]}
            return postprocess(parsed) if postprocess else parsed
        result = _do_golden_quotes(mock_call_fn, _make_messages(10))
        assert len(result['golden_quotes']) == 1

    def test_ai_returns_none_marks_error(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            return postprocess(None)
        result = _do_golden_quotes(mock_call_fn, _make_messages(10))
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"


class TestDoChatQuality:
    def test_empty_messages(self):
        result = _do_chat_quality(lambda *a, **kw: None, [])
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"

    def test_with_mock_call_fn(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            parsed = {'title': '热闹群', 'subtitle': '5人', 'dimensions': [{'name': '活跃度', 'percentage': 80}], 'summary': '很活跃'}
            return postprocess(parsed) if postprocess else parsed
        result = _do_chat_quality(mock_call_fn, _make_messages(10))
        assert result['title'] == '热闹群'

    def test_ai_returns_none_marks_error(self):
        def mock_call_fn(system, user, expect_json=True, json_schema=None, postprocess=None):
            return postprocess(None)
        result = _do_chat_quality(mock_call_fn, _make_messages(10))
        assert result.get("__error__") == "ai_returned_empty_or_invalid_json"


# ── GroupAIAnalyzer 同步/异步薄包装 ─────────────────────────

class TestGroupAIAnalyzerThinWrappers:
    def test_no_api_key_returns_empty(self):
        """无 API Key 时返回 __error__ 标记，reason 应为具体 'API key 未配置'（不再用通用 reason 覆盖）"""
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = analyzer.generate_summary(_make_messages(5))
        assert result.get("__error__") == "API key 未配置"

    def test_no_api_key_keywords(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = analyzer.extract_keywords(_make_messages(5))
        assert result.get("__error__") == "API key 未配置"

    def test_no_api_key_user_titles(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = analyzer.analyze_user_titles(_make_messages(5))
        assert result.get("__error__") == "API key 未配置"

    def test_no_api_key_golden_quotes(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = analyzer.analyze_golden_quotes(_make_messages(5))
        assert result.get("__error__") == "API key 未配置"

    def test_no_api_key_chat_quality(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = analyzer.analyze_chat_quality(_make_messages(5))
        assert result.get("__error__") == "API key 未配置"

    def test_full_analysis_no_api_key(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = analyzer.full_analysis(_make_messages(5))
        assert 'summary' in result
        assert 'keywords' in result
        assert 'user_titles' in result
        assert 'golden_quotes' in result
        assert 'chat_quality' in result
        # 5 个子分析都应标记为 __error__，且 reason 是具体 'API key 未配置'
        for key in ('summary', 'keywords', 'user_titles', 'golden_quotes', 'chat_quality'):
            assert result[key].get("__error__") == "API key 未配置"


# ── _call_ai_with_retry 重试逻辑 ─────────────────────────────

class TestCallAIWithRetry:
    def test_retry_first_fail_second_success(self):
        """第一次 _call_ai 返回 None，第二次返回有效 JSON"""
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        call_count = 0

        def fake_call_ai(system, user, temperature=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # 第一次失败
            return '{"summary": "ok", "topics": [], "key_points": [], "action_items": []}'

        with patch.object(analyzer, '_call_ai', side_effect=fake_call_ai), \
             patch('chatlens.core.ai_analyzer.time.sleep'):
            result = analyzer._call_ai_with_retry('sys', 'user', expect_json=True)
        # 第二次成功，返回 (raw, parsed)
        assert result[1] is not None
        assert result[1]['summary'] == 'ok'
        assert call_count == 2

    def test_no_api_key_returns_empty(self):
        """无 API Key 时直接返回 (None, {'__error__': 'API key 未配置'})"""
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = analyzer._call_ai_with_retry('sys', 'user', expect_json=True)
        assert result[0] is None
        assert result[1] == {'__error__': 'API key 未配置'}

    def test_no_api_key_with_postprocess(self):
        """无 API Key 时有 postprocess 则调用 postprocess({'__error__': 'API key 未配置'})"""
        analyzer = GroupAIAnalyzer({'api_key': ''})
        pp = MagicMock(return_value={'fallback': True})
        result = analyzer._call_ai_with_retry('sys', 'user', postprocess=pp)
        pp.assert_called_once_with({'__error__': 'API key 未配置'})
        assert result == {'fallback': True}


# ── _acall_ai_with_retry 异步重试逻辑 ─────────────────────────

class TestACallAIWithRetry:
    def test_async_retry_first_fail_second_success(self):
        """第一次 _acall_ai 返回 None，第二次返回有效 JSON"""
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        call_count = 0

        async def fake_acall_ai(system, user, temperature=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return '{"summary": "async ok", "topics": [], "key_points": [], "action_items": []}'

        with patch.object(analyzer, '_acall_ai', side_effect=fake_acall_ai), \
             patch('chatlens.core.ai_analyzer.asyncio.sleep', new_callable=AsyncMock):
            result = asyncio.get_event_loop().run_until_complete(
                analyzer._acall_ai_with_retry('sys', 'user', expect_json=True)
            )
        assert result[1] is not None
        assert result[1]['summary'] == 'async ok'
        assert call_count == 2

    def test_async_no_api_key_returns_empty(self):
        """无 API Key 时异步调用返回 (None, {'__error__': 'API key 未配置'})"""
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = asyncio.get_event_loop().run_until_complete(
            analyzer._acall_ai_with_retry('sys', 'user', expect_json=True)
        )
        assert result[0] is None
        assert result[1] == {'__error__': 'API key 未配置'}


# ── 有 API Key 时各分析方法的正常流程 ─────────────────────────

def _mock_retry_return(data):
    """构造 _call_ai_with_retry 的 mock 返回值（有 postprocess 时直接返回 data）"""
    return data


class TestGenerateSummaryWithAPIKey:
    def test_with_api_key(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        expected = {'summary': 'AI summary', 'topics': ['t1'], 'key_points': [], 'action_items': []}
        with patch.object(analyzer, '_call_ai_with_retry', return_value=expected):
            result = analyzer.generate_summary(_make_messages(10))
        assert result['summary'] == 'AI summary'
        assert result['topics'] == ['t1']


class TestExtractKeywordsWithAPIKey:
    def test_with_api_key(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        expected = {'keywords': [{'word': 'AI', 'relevance': 9}], 'hot_topics': []}
        with patch.object(analyzer, '_call_ai_with_retry', return_value=expected):
            result = analyzer.extract_keywords(_make_messages(10))
        assert len(result['keywords']) == 1
        assert result['keywords'][0]['word'] == 'AI'


class TestAnalyzeUserTitlesWithAPIKey:
    def test_with_api_key(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        expected = {'user_titles': [{'name': 'Alice', 'title': '话痨', 'mbti': 'ENFP', 'sbti': '快乐小狗', 'acgti': '元气主角'}]}
        with patch.object(analyzer, '_call_ai_with_retry', return_value=expected):
            result = analyzer.analyze_user_titles(_make_messages(20))
        assert len(result['user_titles']) == 1
        assert result['user_titles'][0]['title'] == '话痨'


class TestAnalyzeGoldenQuotesWithAPIKey:
    def test_with_api_key(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        expected = {'golden_quotes': [{'content': 'Insightful!', 'sender': 'Bob', 'reason': 'deep'}]}
        with patch.object(analyzer, '_call_ai_with_retry', return_value=expected):
            result = analyzer.analyze_golden_quotes(_make_messages(10))
        assert len(result['golden_quotes']) == 1
        assert result['golden_quotes'][0]['content'] == 'Insightful!'


class TestAnalyzeChatQualityWithAPIKey:
    def test_with_api_key(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        expected = {'title': '热闹群', 'subtitle': '5人', 'dimensions': [{'name': '活跃度', 'percentage': 80, 'color': '#e07850'}], 'summary': '很活跃'}
        with patch.object(analyzer, '_call_ai_with_retry', return_value=expected):
            result = analyzer.analyze_chat_quality(_make_messages(10))
        assert result['title'] == '热闹群'
        assert result['dimensions'][0]['name'] == '活跃度'


class TestFullAnalysisWithAPIKey:
    def test_with_api_key(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        summary_r = {'summary': 'S', 'topics': [], 'key_points': [], 'action_items': []}
        keywords_r = {'keywords': [], 'hot_topics': []}
        titles_r = {'user_titles': []}
        quotes_r = {'golden_quotes': []}
        quality_r = {'title': 'T', 'subtitle': '', 'dimensions': [], 'summary': ''}

        with patch.object(analyzer, '_call_ai_with_retry', side_effect=[summary_r, keywords_r, titles_r, quotes_r, quality_r]):
            result = analyzer.full_analysis(_make_messages(10))

        assert result['summary']['summary'] == 'S'
        assert result['keywords'] == keywords_r
        assert result['user_titles'] == titles_r
        assert result['golden_quotes'] == quotes_r
        assert result['chat_quality'] == quality_r


class TestAFullAnalysisWithAPIKey:
    def test_afull_analysis(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key-12345'})
        summary_r = {'summary': 'AS', 'topics': [], 'key_points': [], 'action_items': []}
        keywords_r = {'keywords': [], 'hot_topics': []}
        titles_r = {'user_titles': []}
        quotes_r = {'golden_quotes': []}
        quality_r = {'title': 'AT', 'subtitle': '', 'dimensions': [], 'summary': ''}

        async def fake_acall_retry(system, user, expect_json=True, json_schema=None, postprocess=None):
            # 按调用顺序返回不同结果
            results = [summary_r, keywords_r, titles_r, quotes_r, quality_r]
            idx = fake_acall_retry.call_count
            fake_acall_retry.call_count += 1
            return results[idx]

        fake_acall_retry.call_count = 0

        with patch.object(analyzer, '_acall_ai_with_retry', side_effect=fake_acall_retry):
            result = asyncio.get_event_loop().run_until_complete(
                analyzer.afull_analysis(_make_messages(10))
            )

        assert result['summary']['summary'] == 'AS'
        assert result['chat_quality']['title'] == 'AT'
        assert 'keywords' in result
        assert 'user_titles' in result
        assert 'golden_quotes' in result


# ── inject_personality 提示词构建 ─────────────────────────────

class TestInjectPersonality:
    def test_injects_head_and_tail(self):
        from chatlens.core._analysis_utils import inject_personality
        system_prompt = "请以 JSON 格式返回分析结果。"
        result = inject_personality(system_prompt, "summary")
        assert "洞察力" in result  # head 内容
        assert "人味" in result  # tail 内容

    def test_weave_after_json_line(self):
        from chatlens.core._analysis_utils import inject_personality
        system_prompt = "请以 JSON 格式返回分析结果。"
        result = inject_personality(system_prompt, "summary")
        assert "独特的视角" in result  # weave 内容

    def test_unknown_type_uses_default(self):
        from chatlens.core._analysis_utils import inject_personality
        system_prompt = "请以 JSON 格式返回。"
        result = inject_personality(system_prompt, "nonexistent_type")
        # 默认使用 summary 模板
        assert "洞察力" in result

    def test_preserves_original_content(self):
        from chatlens.core._analysis_utils import inject_personality
        system_prompt = "IMPORTANT_ORIGINAL_CONTENT"
        result = inject_personality(system_prompt, "keywords")
        assert "IMPORTANT_ORIGINAL_CONTENT" in result


# ── parse_json_response 响应解析 ─────────────────────────────

class TestParseJsonResponse:
    def test_valid_json(self):
        from chatlens.core._analysis_utils import parse_json_response
        raw = '{"summary": "test", "topics": []}'
        result = parse_json_response(raw)
        assert result is not None
        assert result['summary'] == 'test'

    def test_json_with_markdown_code_block(self):
        from chatlens.core._analysis_utils import parse_json_response
        raw = '```json\n{"summary": "md", "topics": []}\n```'
        result = parse_json_response(raw)
        assert result is not None
        assert result['summary'] == 'md'

    def test_json_with_plain_code_block(self):
        from chatlens.core._analysis_utils import parse_json_response
        raw = '```\n{"summary": "plain", "topics": []}\n```'
        result = parse_json_response(raw)
        assert result is not None
        assert result['summary'] == 'plain'

    def test_invalid_json_returns_none(self):
        from chatlens.core._analysis_utils import parse_json_response
        raw = 'this is not json at all'
        result = parse_json_response(raw)
        assert result is None

    def test_empty_response_returns_none(self):
        from chatlens.core._analysis_utils import parse_json_response
        assert parse_json_response('') is None
        assert parse_json_response(None) is None
        assert parse_json_response('   ') is None

    def test_json_embedded_in_text(self):
        from chatlens.core._analysis_utils import parse_json_response
        raw = 'Here is the result: {"summary": "embedded", "topics": []} end'
        result = parse_json_response(raw)
        assert result is not None
        assert result['summary'] == 'embedded'

    def test_json_list_parsed_directly(self):
        from chatlens.core._analysis_utils import parse_json_response
        raw = '[{"word": "AI", "relevance": 8}]'
        result = parse_json_response(raw)
        assert result is not None
        # json.loads 直接解析成功，返回原始列表
        assert isinstance(result, list)
        assert result[0]['word'] == 'AI'

    def test_with_schema_fallback(self):
        from chatlens.core._analysis_utils import parse_json_response
        # 非法 JSON 但包含可 regex 提取的字段
        raw = 'Some text "summary": "regex extracted" more text'
        schema = {"summary": "", "topics": []}
        result = parse_json_response(raw, schema)
        assert result is not None
        assert result['summary'] == 'regex extracted'


# ── _call_ai 同步 API 调用 ─────────────────────────────────

class TestCallAI:
    def test_no_client_returns_none(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = analyzer._call_ai('sys', 'user')
        assert result is None

    def test_openai_compatible_call(self):
        analyzer = GroupAIAnalyzer({
            'api_key': 'test-key',
            'base_url': 'https://api.test.com',
            'model': 'test-model',
        })
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = '{"result": "ok"}'
        mock_client.chat.completions.create.return_value = mock_resp

        with patch.object(analyzer, '_get_client', return_value=mock_client):
            result = analyzer._call_ai('system prompt', 'user prompt')
        assert result == '{"result": "ok"}'
        mock_client.chat.completions.create.assert_called_once()

    def test_api_call_failure_returns_none(self):
        analyzer = GroupAIAnalyzer({
            'api_key': 'test-key',
            'base_url': 'https://api.test.com',
            'model': 'test-model',
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        with patch.object(analyzer, '_get_client', return_value=mock_client):
            result = analyzer._call_ai('sys', 'user')
        assert result is None

    def test_custom_temperature(self):
        analyzer = GroupAIAnalyzer({
            'api_key': 'test-key',
            'model': 'test-model',
            'temperature': 0.9,
        })
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = 'ok'
        mock_client.chat.completions.create.return_value = mock_resp

        with patch.object(analyzer, '_get_client', return_value=mock_client):
            analyzer._call_ai('sys', 'user', temperature=0.3)
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get('temperature') == 0.3


# ── _acall_ai 异步 API 调用 ─────────────────────────────────

class TestACallAI:
    def test_no_async_client_returns_none(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        result = asyncio.get_event_loop().run_until_complete(
            analyzer._acall_ai('sys', 'user')
        )
        assert result is None

    def test_async_openai_call(self):
        analyzer = GroupAIAnalyzer({
            'api_key': 'test-key',
            'base_url': 'https://api.test.com',
            'model': 'test-model',
        })
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = '{"async": true}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        with patch.object(analyzer, '_get_async_client', return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                analyzer._acall_ai('sys', 'user')
            )
        assert result == '{"async": true}'

    def test_async_api_failure_returns_none(self):
        analyzer = GroupAIAnalyzer({
            'api_key': 'test-key',
            'model': 'test-model',
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("async error"))

        with patch.object(analyzer, '_get_async_client', return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                analyzer._acall_ai('sys', 'user')
            )
        assert result is None


# ── _get_client / _get_async_client 客户端初始化 ─────────────

class TestGetClient:
    def test_no_api_key_returns_none(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        assert analyzer._get_client() is None

    def test_client_cached(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key', 'base_url': 'https://api.test.com'})
        mock_client = MagicMock()
        analyzer._client = mock_client
        assert analyzer._get_client() is mock_client

    def test_client_init_with_openai(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key', 'base_url': 'https://api.test.com'})
        # 测试缓存逻辑：设置 _client 后再次获取应返回同一实例
        analyzer._client = None
        mock_openai = MagicMock()
        mock_instance = MagicMock()
        mock_openai.OpenAI.return_value = mock_instance
        with patch.dict('sys.modules', {'openai': mock_openai}):
            result = analyzer._get_client()
            # 如果 openai 可导入则返回客户端，否则返回 None
            # 此处主要验证缓存机制
        analyzer._client = mock_instance
        assert analyzer._get_client() is mock_instance


class TestGetAsyncClient:
    def test_no_api_key_returns_none(self):
        analyzer = GroupAIAnalyzer({'api_key': ''})
        assert analyzer._get_async_client() is None

    def test_async_client_cached(self):
        analyzer = GroupAIAnalyzer({'api_key': 'test-key'})
        mock_client = MagicMock()
        analyzer._async_client = mock_client
        assert analyzer._get_async_client() is mock_client


# ── GroupAIAnalyzer 初始化 ─────────────────────────────────

class TestGroupAIAnalyzerInit:
    def test_default_values(self):
        analyzer = GroupAIAnalyzer({})
        assert analyzer.provider == 'deepseek'
        assert analyzer.api_key == ''
        assert analyzer.base_url == ''
        assert analyzer.model == ''
        assert analyzer.temperature == 0.7
        assert analyzer.max_tokens == 4096
        assert analyzer._client is None
        assert analyzer._async_client is None

    def test_custom_config(self):
        config = {
            'provider': 'ollama',
            'api_key': 'sk-123',
            'base_url': 'http://localhost:11434',
            'model': 'llama3',
            'temperature': 0.5,
            'max_tokens': 2048,
        }
        analyzer = GroupAIAnalyzer(config)
        assert analyzer.provider == 'ollama'
        assert analyzer.api_key == 'sk-123'
        assert analyzer.base_url == 'http://localhost:11434'
        assert analyzer.model == 'llama3'
        assert analyzer.temperature == 0.5
        assert analyzer.max_tokens == 2048


# ── AI 失败原因透传：429 / 认证 / JSON 解析失败 ────────────────

class TestAIErrorPropagation:
    """验证 _call_ai 抛错时，分类后的 reason 透传到 postprocess 返回的 __error__ 字段。"""

    def _make_analyzer(self):
        return GroupAIAnalyzer({
            'api_key': 'test-key-12345',
            'base_url': 'https://api.test.com',
            'model': 'test-model',
        })

    def test_rate_limit_429_propagates_reason(self):
        """openai.RateLimitError (429) → __error__ 包含 '限流' 和 '429'"""
        from openai import RateLimitError
        analyzer = self._make_analyzer()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RateLimitError(
            "Rate limit hit", response=MagicMock(), body=None
        )
        pp = MagicMock(return_value={'forwarded': True})

        with patch.object(analyzer, '_get_client', return_value=mock_client), \
             patch('chatlens.core.ai_analyzer.time.sleep'):
            result = analyzer._call_ai_with_retry('sys', 'user', postprocess=pp)

        # postprocess 必须用含 __error__ 的 dict 调用
        pp.assert_called_once()
        forwarded = pp.call_args[0][0]
        assert '__error__' in forwarded
        reason = forwarded['__error__']
        assert '限流' in reason or '429' in reason, f"reason 不含限流/429: {reason!r}"
        assert result == {'forwarded': True}

    def test_authentication_error_propagates_reason(self):
        """openai.AuthenticationError → __error__ 包含 '认证失败'"""
        from openai import AuthenticationError
        analyzer = self._make_analyzer()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = AuthenticationError(
            "Invalid API key", response=MagicMock(), body=None
        )
        pp = MagicMock(return_value={'forwarded': True})

        with patch.object(analyzer, '_get_client', return_value=mock_client), \
             patch('chatlens.core.ai_analyzer.time.sleep'):
            result = analyzer._call_ai_with_retry('sys', 'user', postprocess=pp)

        forwarded = pp.call_args[0][0]
        assert '__error__' in forwarded
        reason = forwarded['__error__']
        assert '认证失败' in reason, f"reason 不含'认证失败': {reason!r}"

    def test_json_parse_failure_propagates_reason(self):
        """_call_ai 返回有效字符串但 parse_json_response 返回 None → __error__ 包含 'JSON'"""
        analyzer = self._make_analyzer()
        # _call_ai 每次都返回非空字符串，但 parse_json_response 都返回 None
        def fake_call_ai(system, user, temperature=None):
            return 'this is not json at all'

        pp = MagicMock(return_value={'forwarded': True})

        with patch.object(analyzer, '_call_ai', side_effect=fake_call_ai), \
             patch('chatlens.core.ai_analyzer.parse_json_response', return_value=None), \
             patch('chatlens.core.ai_analyzer.time.sleep'):
            result = analyzer._call_ai_with_retry('sys', 'user', postprocess=pp)

        forwarded = pp.call_args[0][0]
        assert '__error__' in forwarded
        reason = forwarded['__error__']
        assert 'JSON' in reason, f"reason 应包含 'JSON'，实际: {reason!r}"


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

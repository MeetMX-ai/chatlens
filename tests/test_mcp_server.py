"""MCP Server 单元测试 — 纯逻辑函数（不依赖 HTTP 服务器）"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from chatlens.plugins.mcp.mcp_server import (
    set_service, get_service,
    _parse_ai_data_json, _parse_ai_data_legacy, _parse_ai_data,
    _get_server_url,
    _validate_group_name, _validate_keyword, _validate_time,
    _fmt_msg, _http_post, _http_get, _http_delete,
    MCPService,
    chatlens_list_groups, chatlens_list_talkers, chatlens_load_data,
    chatlens_analyze_group, chatlens_get_recent_messages,
    chatlens_search_messages, chatlens_get_messages_for_ai,
    chatlens_delete_data, chatlens_ai_analyze,
    chatlens_get_user_titles, chatlens_get_golden_quotes,
    chatlens_get_chat_quality,
    chatlens_schedule_create, chatlens_schedule_list,
    chatlens_schedule_trigger, chatlens_schedule_delete,
    chatlens_schedule_toggle,
    chatlens_check_pending, chatlens_submit_analysis,
    chatlens_refresh_data,
    chatlog_status, setup,
    AnalysisResult, AIAnalysisResult,
)
from chatlens.core.models import ChatMessage


# ── set_service / get_service 全局状态管理 ────────────────────

class TestServiceState:
    def setup_method(self):
        """每个测试前清空全局状态"""
        set_service(None)

    def teardown_method(self):
        """每个测试后清空全局状态"""
        set_service(None)

    def test_set_and_get(self):
        svc = MagicMock()
        set_service(svc)
        assert get_service() is svc

    def test_get_default_none(self):
        assert get_service() is None

    def test_set_none_clears(self):
        svc = MagicMock()
        set_service(svc)
        assert get_service() is svc
        set_service(None)
        assert get_service() is None

    def test_replace_service(self):
        svc1 = MagicMock(name='svc1')
        svc2 = MagicMock(name='svc2')
        set_service(svc1)
        set_service(svc2)
        assert get_service() is svc2


# ── _parse_ai_data_json ──────────────────────────────────────

class TestParseAIDataJson:
    def test_empty_string(self):
        assert _parse_ai_data_json('') == {}

    def test_empty_json_object(self):
        assert _parse_ai_data_json('{}') == {}

    def test_valid_json(self):
        data = '{"summary": "test", "topics": []}'
        result = _parse_ai_data_json(data)
        assert result['summary'] == 'test'

    def test_invalid_json(self):
        assert _parse_ai_data_json('not json') == {}

    def test_dict_input(self):
        data = {'summary': 'hello'}
        result = _parse_ai_data_json(data)
        assert result['summary'] == 'hello'

    def test_user_titles_sbti_acgti(self):
        data = json.dumps({
            'user_titles': [{'name': 'A', 'title': '话痨', 'mbti': 'ENFP'}]
        })
        result = _parse_ai_data_json(data)
        titles = result['user_titles']['user_titles']
        assert titles[0]['sbti'] == '快乐小狗'
        assert titles[0]['acgti'] == '元气团宠型'

    def test_user_titles_unknown_mbti(self):
        data = json.dumps({
            'user_titles': [{'name': 'B', 'title': '神秘人', 'mbti': 'XXXX'}]
        })
        result = _parse_ai_data_json(data)
        titles = result['user_titles']['user_titles']
        assert titles[0]['sbti'] == '未知生物'
        assert titles[0]['acgti'] == '未知角色'

    def test_user_titles_dict_wrapping(self):
        """user_titles 为 dict 时应提取内部列表"""
        data = json.dumps({
            'user_titles': {'user_titles': [{'name': 'A', 'title': '话痨', 'mbti': 'INTJ'}]}
        })
        result = _parse_ai_data_json(data)
        titles = result['user_titles']['user_titles']
        assert titles[0]['mbti'] == 'INTJ'

    def test_chat_quality_default_colors(self):
        data = json.dumps({
            'chat_quality': {
                'dimensions': [
                    {'name': '活跃度', 'percentage': 80},
                    {'name': '深度', 'percentage': 60},
                ]
            }
        })
        result = _parse_ai_data_json(data)
        dims = result['chat_quality']['dimensions']
        assert dims[0]['color'] == '#e07850'
        assert dims[1]['color'] == '#d4a853'

    def test_chat_quality_existing_color_preserved(self):
        data = json.dumps({
            'chat_quality': {
                'dimensions': [{'name': '活跃度', 'percentage': 80, 'color': '#custom'}]
            }
        })
        result = _parse_ai_data_json(data)
        assert result['chat_quality']['dimensions'][0]['color'] == '#custom'


# ── _parse_ai_data_legacy ────────────────────────────────────

class TestParseAIDataLegacy:
    def test_all_empty(self):
        assert _parse_ai_data_legacy() == {}

    def test_ai_summary(self):
        result = _parse_ai_data_legacy(ai_summary='Test summary')
        assert result['summary']['summary'] == 'Test summary'

    def test_ai_topics(self):
        topics = [{'name': 't1', 'description': 'd1'}]
        result = _parse_ai_data_legacy(ai_summary='S', ai_topics=json.dumps(topics))
        assert result['summary']['topics'] == topics

    def test_ai_user_titles(self):
        titles = [{'name': 'A', 'title': '话痨', 'mbti': 'ENFP'}]
        result = _parse_ai_data_legacy(ai_user_titles=json.dumps(titles))
        assert result['user_titles']['user_titles'][0]['sbti'] == '快乐小狗'

    def test_ai_golden_quotes(self):
        quotes = [{'content': 'Great!', 'sender': 'B', 'reason': 'deep'}]
        result = _parse_ai_data_legacy(ai_golden_quotes=json.dumps(quotes))
        assert result['golden_quotes']['golden_quotes'][0]['content'] == 'Great!'

    def test_ai_chat_quality(self):
        quality = {'title': '热闹', 'dimensions': [{'name': '活跃度', 'percentage': 80}]}
        result = _parse_ai_data_legacy(ai_chat_quality=json.dumps(quality))
        assert result['chat_quality']['dimensions'][0]['color'] == '#e07850'

    def test_ai_keywords(self):
        keywords = [{'word': 'AI', 'relevance': 9}]
        result = _parse_ai_data_legacy(ai_keywords=json.dumps(keywords))
        assert result['keywords']['keywords'][0]['word'] == 'AI'

    def test_invalid_json_fields(self):
        """无效 JSON 字段应被忽略"""
        result = _parse_ai_data_legacy(ai_topics='not json', ai_user_titles='bad')
        assert result == {}


# ── _parse_ai_data 调度器 ────────────────────────────────────

class TestParseAIData:
    def test_ai_data_priority(self):
        """ai_data 优先于 legacy 参数"""
        ai_data = json.dumps({'summary': 'from ai_data'})
        result = _parse_ai_data(ai_data=ai_data, ai_summary='from legacy')
        assert result['summary'] == 'from ai_data'

    def test_fallback_to_legacy(self):
        """ai_data 为空时回退到 legacy 参数"""
        result = _parse_ai_data(ai_data='{}', ai_summary='legacy summary')
        assert result['summary']['summary'] == 'legacy summary'

    def test_empty_all(self):
        result = _parse_ai_data()
        assert result == {}

    def test_ai_data_invalid_falls_to_legacy(self):
        """ai_data 无效 JSON 时回退到 legacy"""
        result = _parse_ai_data(ai_data='not json', ai_summary='fallback')
        assert result['summary']['summary'] == 'fallback'


# ── _validate_group_name ─────────────────────────────────────

class TestValidateGroupName:
    def test_valid_name(self):
        assert _validate_group_name('测试群') == '测试群'

    def test_strips_whitespace(self):
        assert _validate_group_name('  群名  ') == '群名'

    def test_empty_raises(self):
        import pytest
        with pytest.raises(ValueError, match='不能为空'):
            _validate_group_name('')

    def test_path_traversal_dotdot(self):
        import pytest
        with pytest.raises(ValueError, match='非法字符'):
            _validate_group_name('../etc/passwd')

    def test_path_traversal_slash(self):
        import pytest
        with pytest.raises(ValueError, match='非法字符'):
            _validate_group_name('foo/bar')

    def test_path_traversal_backslash(self):
        import pytest
        with pytest.raises(ValueError, match='非法字符'):
            _validate_group_name('foo\\bar')

    def test_too_long(self):
        import pytest
        with pytest.raises(ValueError, match='过长'):
            _validate_group_name('x' * 201)


# ── _get_server_url ──────────────────────────────────────────

class TestGetServerUrl:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_default_url_no_service(self):
        """无 service 时使用默认值"""
        with patch('chatlens._defaults.DEFAULT_SERVER_HOST', 'localhost'), \
             patch('chatlens._defaults.DEFAULT_SERVER_PORT', 8080):
            url = _get_server_url()
        assert url == 'http://localhost:8080'

    def test_custom_config(self):
        """有 service 且 config 中有自定义 host/port"""
        svc = MagicMock()
        svc.ga = MagicMock()
        svc.ga.config = {'server': {'host': '192.168.1.1', 'port': 9090}}
        set_service(svc)
        with patch('chatlens._defaults.DEFAULT_SERVER_HOST', 'localhost'), \
             patch('chatlens._defaults.DEFAULT_SERVER_PORT', 8080):
            url = _get_server_url()
        assert url == 'http://192.168.1.1:9090'

    def test_partial_config_uses_defaults(self):
        """config 中只有 host 时 port 使用默认值"""
        svc = MagicMock()
        svc.ga = MagicMock()
        svc.ga.config = {'server': {'host': 'myhost'}}
        set_service(svc)
        with patch('chatlens._defaults.DEFAULT_SERVER_HOST', 'localhost'), \
             patch('chatlens._defaults.DEFAULT_SERVER_PORT', 8080):
            url = _get_server_url()
        assert url == 'http://myhost:8080'

    def test_service_no_config(self):
        """有 service 但 ga.config 为 None 时使用默认值"""
        svc = MagicMock()
        svc.ga = MagicMock()
        svc.ga.config = None
        set_service(svc)
        with patch('chatlens._defaults.DEFAULT_SERVER_HOST', 'localhost'), \
             patch('chatlens._defaults.DEFAULT_SERVER_PORT', 8080):
            url = _get_server_url()
        assert url == 'http://localhost:8080'


# ── _validate_keyword ─────────────────────────────────────────

class TestValidateKeyword:
    def test_valid_keyword(self):
        assert _validate_keyword('测试') == '测试'

    def test_strips_whitespace(self):
        assert _validate_keyword('  关键词  ') == '关键词'

    def test_empty_raises(self):
        import pytest
        with pytest.raises(ValueError, match='不能为空'):
            _validate_keyword('')

    def test_whitespace_only_raises(self):
        import pytest
        with pytest.raises(ValueError, match='不能为空'):
            _validate_keyword('   ')

    def test_too_long(self):
        import pytest
        with pytest.raises(ValueError, match='过长'):
            _validate_keyword('x' * 501)

    def test_max_length_ok(self):
        assert _validate_keyword('x' * 500) == 'x' * 500


# ── _validate_time ───────────────────────────────────────────

class TestValidateTime:
    def test_valid_time(self):
        _validate_time(8, 30)

    def test_midnight(self):
        _validate_time(0, 0)

    def test_end_of_day(self):
        _validate_time(23, 59)

    def test_hour_too_low(self):
        import pytest
        with pytest.raises(ValueError, match='hour'):
            _validate_time(-1, 0)

    def test_hour_too_high(self):
        import pytest
        with pytest.raises(ValueError, match='hour'):
            _validate_time(24, 0)

    def test_minute_too_low(self):
        import pytest
        with pytest.raises(ValueError, match='minute'):
            _validate_time(0, -1)

    def test_minute_too_high(self):
        import pytest
        with pytest.raises(ValueError, match='minute'):
            _validate_time(0, 60)


# ── _fmt_msg ─────────────────────────────────────────────────

class TestFmtMsg:
    def _make_msg(self, **kwargs):
        defaults = dict(
            sender='张三', content='你好', msg_type='text',
            msg_attr='normal', timestamp='2024-01-01 10:30:00',
            group_name='测试群', sender_remark='', quote_content='',
        )
        defaults.update(kwargs)
        return ChatMessage(**defaults)

    def test_text_message(self):
        m = self._make_msg()
        assert _fmt_msg(m) == '[10:30] 张三: 你好'

    def test_system_message_returns_empty(self):
        m = self._make_msg(msg_attr='system')
        assert _fmt_msg(m) == ''

    def test_image_message(self):
        m = self._make_msg(msg_type='image', content='')
        assert _fmt_msg(m) == '[10:30] 张三: [图片]'

    def test_voice_message(self):
        m = self._make_msg(msg_type='voice', content='')
        assert _fmt_msg(m) == '[10:30] 张三: [语音]'

    def test_quote_message(self):
        m = self._make_msg(msg_type='quote', content='回复内容', quote_content='被引用的内容很长也没关系')
        result = _fmt_msg(m)
        assert '(引用)' in result
        assert '回复内容' in result

    def test_emotion_message(self):
        m = self._make_msg(msg_type='emotion', content='')
        assert _fmt_msg(m) == '[10:30] 张三: [表情]'

    def test_unknown_msg_type(self):
        m = self._make_msg(msg_type='video', content='')
        assert _fmt_msg(m) == '[10:30] 张三: [video]'

    def test_sender_remark_preferred(self):
        m = self._make_msg(sender_remark='三哥')
        assert _fmt_msg(m) == '[10:30] 三哥: 你好'

    def test_no_timestamp(self):
        m = self._make_msg(timestamp='')
        assert _fmt_msg(m) == '[] 张三: 你好'

    def test_no_sender(self):
        m = self._make_msg(sender='', sender_remark='')
        assert _fmt_msg(m) == '[10:30] 未知: 你好'

    def test_bad_timestamp_format(self):
        m = self._make_msg(timestamp='bad-format')
        assert _fmt_msg(m) == '[] 张三: 你好'


# ── _http_post / _http_get / _http_delete ────────────────────

class TestHttpFunctions:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_http_post_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "task_id": "abc"}
        with patch('chatlens.plugins.mcp.mcp_server.httpx.post', return_value=mock_resp):
            result = _http_post("/api/test", {"key": "val"})
        assert result["success"] is True
        assert result["task_id"] == "abc"

    def test_http_post_connection_error(self):
        with patch('chatlens.plugins.mcp.mcp_server.httpx.post', side_effect=httpx.ConnectError("refused")):
            result = _http_post("/api/test", {"key": "val"})
        assert result["success"] is False
        assert "refused" in result["error"]

    def test_http_post_timeout(self):
        with patch('chatlens.plugins.mcp.mcp_server.httpx.post', side_effect=httpx.TimeoutException("timed out")):
            result = _http_post("/api/test", {"key": "val"})
        assert result["success"] is False
        assert "timed out" in result["error"]

    def test_http_post_json_decode_error(self):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("err", "doc", 0)
        with patch('chatlens.plugins.mcp.mcp_server.httpx.post', return_value=mock_resp):
            result = _http_post("/api/test", {"key": "val"})
        assert result["success"] is False

    def test_http_get_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tasks": []}
        with patch('chatlens.plugins.mcp.mcp_server.httpx.get', return_value=mock_resp):
            result = _http_get("/api/test")
        assert result["tasks"] == []

    def test_http_get_connection_error(self):
        with patch('chatlens.plugins.mcp.mcp_server.httpx.get', side_effect=httpx.ConnectError("fail")):
            result = _http_get("/api/test")
        assert result["success"] is False

    def test_http_get_timeout(self):
        with patch('chatlens.plugins.mcp.mcp_server.httpx.get', side_effect=httpx.TimeoutException("timeout")):
            result = _http_get("/api/test")
        assert result["success"] is False

    def test_http_delete_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True}
        with patch('chatlens.plugins.mcp.mcp_server.httpx.delete', return_value=mock_resp):
            result = _http_delete("/api/test", {"task_id": "abc"})
        assert result["success"] is True

    def test_http_delete_connection_error(self):
        with patch('chatlens.plugins.mcp.mcp_server.httpx.delete', side_effect=httpx.ConnectError("fail")):
            result = _http_delete("/api/test", {"task_id": "abc"})
        assert result["success"] is False

    def test_http_delete_os_error(self):
        with patch('chatlens.plugins.mcp.mcp_server.httpx.delete', side_effect=OSError("network")):
            result = _http_delete("/api/test", {"task_id": "abc"})
        assert result["success"] is False


# ── MCPService ───────────────────────────────────────────────

class TestMCPService:
    def test_get_messages_from_cache(self):
        ga = MagicMock()
        ga.has_messages.return_value = True
        ga.get_messages.return_value = [MagicMock()]
        svc = MCPService(ga)
        result = svc.get_messages("群1")
        ga.has_messages.assert_called_once_with("群1")
        ga.get_messages.assert_called_once_with("群1")

    def test_get_messages_load_from_file(self):
        ga = MagicMock()
        ga.has_messages.return_value = False
        ga.load_from_file.return_value = [MagicMock()]
        svc = MCPService(ga)
        result = svc.get_messages("群1")
        ga.load_from_file.assert_called_once_with("群1")

    def test_get_stats(self):
        ga = MagicMock()
        ga.has_messages.return_value = True
        ga.get_messages.return_value = [MagicMock()]
        ga.stats_analyzer.analyze.return_value = {"overview": {}}
        svc = MCPService(ga)
        result = svc.get_stats("群1")
        assert result == {"overview": {}}

    def test_get_chatlog(self):
        ga = MagicMock()
        bridge = MagicMock()
        provider = MagicMock()
        provider.bridge = bridge
        ga.get_provider.return_value = provider
        svc = MCPService(ga)
        assert svc.get_chatlog() is bridge

    def test_get_chatlog_no_provider(self):
        ga = MagicMock()
        ga.get_provider.return_value = None
        svc = MCPService(ga)
        assert svc.get_chatlog() is None

    def test_get_provider(self):
        ga = MagicMock()
        ga.get_provider.return_value = "wechat_provider"
        svc = MCPService(ga)
        assert svc.get_provider("wechat") == "wechat_provider"

    def test_get_ai(self):
        ga = MagicMock()
        svc = MCPService(ga)
        assert svc.get_ai() is ga.ai_analyzer

    def test_get_collector_returns_self(self):
        svc = MCPService(MagicMock())
        assert svc.get_collector() is svc

    def test_get_data_files(self):
        ga = MagicMock()
        ga.get_data_files.return_value = [{"group_name": "群1"}]
        svc = MCPService(ga)
        assert svc.get_data_files() == [{"group_name": "群1"}]


# ── Helper: 构建 mock service ────────────────────────────────

def _make_mock_service(messages=None, stats=None, provider=None, ai=None, data_files=None):
    """构建一个完整的 mock MCPService，用于测试工具函数"""
    svc = MagicMock()
    svc.get_messages.return_value = messages or []
    svc.get_stats.return_value = stats or {}
    svc.get_provider.return_value = provider
    svc.get_ai.return_value = ai or MagicMock(api_key=None)
    svc.get_data_files.return_value = data_files or []
    return svc


def _make_mock_provider(available=True, talkers=None, display_name="测试群", messages=None):
    provider = MagicMock()
    provider.is_available.return_value = available
    provider.bridge.get_all_talkers.return_value = talkers or []
    provider.get_display_name.return_value = display_name
    provider.get_messages.return_value = messages or []
    return provider


def _make_msg(sender='张三', content='你好', msg_type='text', msg_attr='normal',
              timestamp='2024-01-01 10:30:00', group_name='测试群',
              sender_remark='', quote_content=''):
    return ChatMessage(
        sender=sender, content=content, msg_type=msg_type,
        msg_attr=msg_attr, timestamp=timestamp, group_name=group_name,
        sender_remark=sender_remark, quote_content=quote_content,
    )


# ── chatlens_list_groups ─────────────────────────────────────

class TestChatlensListGroups:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_no_service(self):
        result = chatlens_list_groups()
        assert '没有' in result or '加载' in result

    def test_no_data_files(self):
        svc = _make_mock_service(data_files=[])
        set_service(svc)
        result = chatlens_list_groups()
        assert '没有' in result

    def test_with_data_files(self):
        files = [
            {"group_name": "群1", "message_count": 100, "collected_at": "2024-01-01"},
            {"group_name": "群2", "message_count": 200, "collected_at": "2024-01-02"},
        ]
        svc = _make_mock_service(data_files=files)
        set_service(svc)
        result = chatlens_list_groups()
        assert '群1' in result
        assert '群2' in result
        assert '2' in result

    def test_with_offset_and_limit(self):
        files = [{"group_name": f"群{i}", "message_count": i * 10, "collected_at": "2024-01-01"} for i in range(5)]
        svc = _make_mock_service(data_files=files)
        set_service(svc)
        result = chatlens_list_groups(offset=1, limit=2)
        assert '群1' in result
        assert '群2' in result
        assert 'has_more: True' in result


# ── chatlens_list_talkers ────────────────────────────────────

class TestChatlensListTalkers:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_no_service(self):
        result = chatlens_list_talkers()
        assert '未初始化' in result

    def test_provider_not_available(self):
        svc = _make_mock_service(provider=_make_mock_provider(available=False))
        set_service(svc)
        result = chatlens_list_talkers()
        assert '不可用' in result

    def test_no_provider(self):
        svc = _make_mock_service(provider=None)
        set_service(svc)
        result = chatlens_list_talkers()
        assert '不可用' in result

    def test_all_talkers(self):
        talkers = [
            {"talker": "g1@chatroom", "is_chatroom": True, "message_count": 100, "display_name": "群聊1"},
            {"talker": "user1", "is_chatroom": False, "message_count": 50, "display_name": "用户1"},
        ]
        provider = _make_mock_provider(talkers=talkers)
        svc = _make_mock_service(provider=provider)
        set_service(svc)
        result = chatlens_list_talkers()
        assert '群聊1' in result
        assert '用户1' in result
        assert '[群]' in result
        assert '[私]' in result

    def test_group_only(self):
        talkers = [
            {"talker": "g1@chatroom", "is_chatroom": True, "message_count": 100, "display_name": "群聊1"},
            {"talker": "user1", "is_chatroom": False, "message_count": 50, "display_name": "用户1"},
        ]
        provider = _make_mock_provider(talkers=talkers)
        svc = _make_mock_service(provider=provider)
        set_service(svc)
        result = chatlens_list_talkers(talker_type="group")
        assert '群聊1' in result
        assert '用户1' not in result

    def test_private_only(self):
        talkers = [
            {"talker": "g1@chatroom", "is_chatroom": True, "message_count": 100, "display_name": "群聊1"},
            {"talker": "user1", "is_chatroom": False, "message_count": 50, "display_name": "用户1"},
        ]
        provider = _make_mock_provider(talkers=talkers)
        svc = _make_mock_service(provider=provider)
        set_service(svc)
        result = chatlens_list_talkers(talker_type="private")
        assert '群聊1' not in result
        assert '用户1' in result

    def test_empty_talkers(self):
        provider = _make_mock_provider(talkers=[])
        svc = _make_mock_service(provider=provider)
        set_service(svc)
        result = chatlens_list_talkers()
        assert '没有找到' in result


# ── chatlens_load_data ───────────────────────────────────────

class TestChatlensLoadData:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_talker_name(self):
        result = chatlens_load_data(talker="")
        assert '参数错误' in result

    def test_no_service(self):
        result = chatlens_load_data(talker="测试群")
        assert '未初始化' in result

    def test_provider_not_available(self):
        svc = _make_mock_service(provider=_make_mock_provider(available=False))
        set_service(svc)
        result = chatlens_load_data(talker="测试群")
        assert '不可用' in result

    def test_no_messages(self):
        provider = _make_mock_provider(messages=[])
        svc = _make_mock_service(provider=provider)
        set_service(svc)
        result = chatlens_load_data(talker="测试群")
        assert '未找到' in result

    def test_successful_load(self):
        msgs = [_make_msg(), _make_msg(content='第二条')]
        provider = _make_mock_provider(messages=msgs)
        svc = _make_mock_service(provider=provider)
        svc.ga = MagicMock()
        set_service(svc)
        result = chatlens_load_data(talker="测试群")
        assert '✅' in result
        assert '2' in result
        svc.ga.set_messages.assert_called_once()
        svc.ga.save_loaded.assert_called_once()


# ── chatlens_analyze_group ───────────────────────────────────

class TestChatlensAnalyzeGroup:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_group_name(self):
        result = chatlens_analyze_group(group_name="")
        assert isinstance(result, AnalysisResult)
        assert '参数错误' in result.markdown

    def test_no_service(self):
        result = chatlens_analyze_group(group_name="测试群")
        assert isinstance(result, AnalysisResult)
        assert '未初始化' in result.markdown

    def test_no_messages(self):
        svc = _make_mock_service(messages=[])
        set_service(svc)
        result = chatlens_analyze_group(group_name="测试群")
        assert isinstance(result, AnalysisResult)
        assert '未找到' in result.markdown

    def test_successful_analysis(self):
        msgs = [_make_msg(), _make_msg()]
        stats = {
            "overview": {
                "total_messages": 100,
                "total_members": 10,
                "avg_messages_per_day": 5.0,
                "time_range": {"start": "2024-01-01", "end": "2024-01-10"},
            },
            "member_stats": [
                {"sender": "张三", "msg_count": 50, "msg_percentage": 50.0},
            ],
            "msg_type_distribution": [
                {"type": "text", "label": "文本", "count": 80, "percentage": 80.0},
            ],
            "hourly_distribution": [
                {"label": "10:00", "count": 20},
            ],
            "keyword_cloud": [
                {"word": "你好", "count": 10},
            ],
        }
        svc = _make_mock_service(messages=msgs, stats=stats)
        set_service(svc)
        result = chatlens_analyze_group(group_name="测试群")
        assert isinstance(result, AnalysisResult)
        assert result.group_name == "测试群"
        assert result.total_messages == 100
        assert result.total_members == 10
        assert '张三' in result.markdown
        assert '你好' in result.markdown

    def test_analysis_no_time_range(self):
        msgs = [_make_msg()]
        stats = {
            "overview": {
                "total_messages": 10,
                "total_members": 2,
                "avg_messages_per_day": 1.0,
                "time_range": {},
            },
        }
        svc = _make_mock_service(messages=msgs, stats=stats)
        set_service(svc)
        result = chatlens_analyze_group(group_name="测试群")
        assert isinstance(result, AnalysisResult)
        assert '时间范围' not in result.markdown


# ── chatlens_get_recent_messages ─────────────────────────────

class TestChatlensGetRecentMessages:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_group_name(self):
        result = chatlens_get_recent_messages(group_name="")
        assert '参数错误' in result

    def test_no_service(self):
        result = chatlens_get_recent_messages(group_name="测试群")
        assert '未初始化' in result

    def test_no_messages(self):
        svc = _make_mock_service(messages=[])
        set_service(svc)
        result = chatlens_get_recent_messages(group_name="测试群")
        assert '未找到' in result

    def test_with_messages(self):
        msgs = [_make_msg(content=f'消息{i}') for i in range(5)]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        result = chatlens_get_recent_messages(group_name="测试群", count=3)
        assert '3 条消息' in result
        assert '消息2' in result

    def test_count_exceeds_total(self):
        msgs = [_make_msg(content='唯一消息')]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        result = chatlens_get_recent_messages(group_name="测试群", count=100)
        assert '1 条消息' in result


# ── chatlens_search_messages ─────────────────────────────────

class TestChatlensSearchMessages:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_group_name(self):
        result = chatlens_search_messages(group_name="", keyword="测试")
        assert '参数错误' in result

    def test_invalid_keyword(self):
        result = chatlens_search_messages(group_name="测试群", keyword="")
        assert '参数错误' in result

    def test_no_service(self):
        result = chatlens_search_messages(group_name="测试群", keyword="你好")
        assert '未初始化' in result

    def test_no_messages(self):
        svc = _make_mock_service(messages=[])
        set_service(svc)
        result = chatlens_search_messages(group_name="测试群", keyword="你好")
        assert '未找到' in result

    def test_search_found(self):
        msgs = [
            _make_msg(content='你好世界'),
            _make_msg(content='再见世界'),
            _make_msg(content='你好啊'),
        ]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        result = chatlens_search_messages(group_name="测试群", keyword="你好")
        assert '2' in result
        assert '你好世界' in result

    def test_search_not_found(self):
        msgs = [_make_msg(content='你好世界')]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        result = chatlens_search_messages(group_name="测试群", keyword="不存在")
        assert '未找到' in result

    def test_search_case_insensitive(self):
        msgs = [_make_msg(content='Hello World')]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        result = chatlens_search_messages(group_name="测试群", keyword="hello")
        assert 'Hello World' in result

    def test_search_skips_system_messages(self):
        msgs = [
            _make_msg(content='你好', msg_attr='normal'),
            _make_msg(content='你好系统', msg_attr='system'),
        ]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        result = chatlens_search_messages(group_name="测试群", keyword="你好")
        assert '1' in result

    def test_search_with_offset(self):
        msgs = [_make_msg(content=f'你好{i}') for i in range(5)]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        result = chatlens_search_messages(group_name="测试群", keyword="你好", offset=2, count=2)
        # offset=2, count=2 → offset+count=4 < total=5, so has_more=True
        assert 'has_more: True' in result


# ── chatlens_get_messages_for_ai ─────────────────────────────

class TestChatlensGetMessagesForAi:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_group_name(self):
        result = chatlens_get_messages_for_ai(group_name="")
        assert '参数错误' in result

    def test_no_service(self):
        result = chatlens_get_messages_for_ai(group_name="测试群")
        assert '未初始化' in result

    def test_no_messages(self):
        svc = _make_mock_service(messages=[])
        set_service(svc)
        result = chatlens_get_messages_for_ai(group_name="测试群")
        assert '未找到' in result

    def test_with_messages(self):
        msgs = [_make_msg(content=f'消息{i}') for i in range(10)]
        provider = _make_mock_provider(display_name="我的群")
        svc = _make_mock_service(messages=msgs, provider=provider)
        set_service(svc)
        result = chatlens_get_messages_for_ai(group_name="测试群", count=5)
        assert '5 条消息' in result
        assert '消息9' in result

    def test_with_offset(self):
        msgs = [_make_msg(content=f'消息{i}') for i in range(10)]
        svc = _make_mock_service(messages=msgs, provider=None)
        set_service(svc)
        result = chatlens_get_messages_for_ai(group_name="测试群", count=5, offset=3)
        assert '5 条消息' in result


# ── chatlens_delete_data ─────────────────────────────────────

class TestChatlensDeleteData:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_group_name(self):
        result = chatlens_delete_data(group_name="")
        assert '参数错误' in result

    def test_no_service(self):
        result = chatlens_delete_data(group_name="测试群")
        assert '未初始化' in result

    def test_delete_success(self):
        svc = _make_mock_service()
        svc.ga.delete_loaded.return_value = True
        set_service(svc)
        result = chatlens_delete_data(group_name="测试群")
        assert '✅' in result

    def test_delete_not_found(self):
        svc = _make_mock_service()
        svc.ga.delete_loaded.return_value = False
        set_service(svc)
        result = chatlens_delete_data(group_name="测试群")
        assert '❌' in result


# ── chatlens_ai_analyze ──────────────────────────────────────

class TestChatlensAiAnalyze:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_group_name(self):
        result = chatlens_ai_analyze(group_name="")
        assert isinstance(result, AIAnalysisResult)
        assert '参数错误' in result.markdown

    def test_no_service(self):
        result = chatlens_ai_analyze(group_name="测试群")
        assert isinstance(result, AIAnalysisResult)
        assert '未初始化' in result.markdown

    def test_no_messages(self):
        svc = _make_mock_service(messages=[])
        set_service(svc)
        result = chatlens_ai_analyze(group_name="测试群")
        assert isinstance(result, AIAnalysisResult)
        assert '未找到' in result.markdown

    def test_unknown_analysis_type(self):
        ai = MagicMock(api_key="test-key")
        msgs = [_make_msg()]
        svc = _make_mock_service(messages=msgs, ai=ai)
        set_service(svc)
        result = chatlens_ai_analyze(group_name="测试群", analysis_type="unknown_type")
        assert isinstance(result, AIAnalysisResult)
        assert '未知分析类型' in result.markdown

    def test_rule_fallback_no_api_key(self):
        msgs = [_make_msg()]
        ai = MagicMock(api_key=None)
        svc = _make_mock_service(messages=msgs, ai=ai)
        set_service(svc)
        with patch('chatlens.core.ai_analyzer.rule_based_analysis') as mock_rule:
            mock_rule.return_value = {"summary": {"summary_text": "测试摘要"}, "user_titles": {}, "golden_quotes": {}, "chat_quality": {}, "keywords": {}}
            result = chatlens_ai_analyze(group_name="测试群", analysis_type="summary")
        assert isinstance(result, AIAnalysisResult)
        assert result.method == "rule"

    def test_ai_full_analysis(self):
        msgs = [_make_msg()]
        ai = MagicMock(api_key="test-key")
        ai.full_analysis.return_value = {"summary": "AI摘要"}
        svc = _make_mock_service(messages=msgs, ai=ai)
        set_service(svc)
        result = chatlens_ai_analyze(group_name="测试群", analysis_type="full")
        assert isinstance(result, AIAnalysisResult)
        assert result.method == "ai"
        assert result.data == {"summary": "AI摘要"}


# ── chatlens_get_user_titles ─────────────────────────────────

class TestChatlensGetUserTitles:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_group_name(self):
        result = chatlens_get_user_titles(group_name="")
        assert '参数错误' in result

    def test_no_service(self):
        result = chatlens_get_user_titles(group_name="测试群")
        assert '未初始化' in result

    def test_no_messages(self):
        svc = _make_mock_service(messages=[])
        set_service(svc)
        result = chatlens_get_user_titles(group_name="测试群")
        assert '未找到' in result

    def test_with_titles(self):
        msgs = [_make_msg()]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        with patch('chatlens.plugins.mcp.mcp_server._ai_or_rule') as mock_ai:
            mock_ai.return_value = ({"user_titles": [{"name": "张三", "title": "话痨", "mbti": "ENFP", "sbti": "快乐小狗", "acgti": "元气团宠型", "reason": "话多"}]}, False)
            result = chatlens_get_user_titles(group_name="测试群")
        assert '话痨' in result
        assert '张三' in result

    def test_no_titles(self):
        msgs = [_make_msg()]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        with patch('chatlens.plugins.mcp.mcp_server._ai_or_rule') as mock_ai:
            mock_ai.return_value = ({"user_titles": []}, False)
            result = chatlens_get_user_titles(group_name="测试群")
        assert '未能生成' in result


# ── chatlens_get_golden_quotes ───────────────────────────────

class TestChatlensGetGoldenQuotes:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_no_service(self):
        result = chatlens_get_golden_quotes(group_name="测试群")
        assert '未初始化' in result

    def test_no_messages(self):
        svc = _make_mock_service(messages=[])
        set_service(svc)
        result = chatlens_get_golden_quotes(group_name="测试群")
        assert '未找到' in result

    def test_with_quotes(self):
        msgs = [_make_msg()]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        with patch('chatlens.plugins.mcp.mcp_server._ai_or_rule') as mock_ai:
            mock_ai.return_value = ({"golden_quotes": [{"content": "金句内容", "sender": "张三", "reason": "深刻"}]}, False)
            result = chatlens_get_golden_quotes(group_name="测试群")
        assert '金句内容' in result
        assert '张三' in result

    def test_no_quotes(self):
        msgs = [_make_msg()]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        with patch('chatlens.plugins.mcp.mcp_server._ai_or_rule') as mock_ai:
            mock_ai.return_value = ({"golden_quotes": []}, False)
            result = chatlens_get_golden_quotes(group_name="测试群")
        assert '未筛选到' in result


# ── chatlens_get_chat_quality ────────────────────────────────

class TestChatlensGetChatQuality:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_no_service(self):
        result = chatlens_get_chat_quality(group_name="测试群")
        assert '未初始化' in result

    def test_no_messages(self):
        svc = _make_mock_service(messages=[])
        set_service(svc)
        result = chatlens_get_chat_quality(group_name="测试群")
        assert '未找到' in result

    def test_with_quality(self):
        msgs = [_make_msg()]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        with patch('chatlens.plugins.mcp.mcp_server._ai_or_rule') as mock_ai:
            mock_ai.return_value = ({
                "title": "热闹非凡",
                "subtitle": "群聊质量报告",
                "dimensions": [{"name": "活跃度", "percentage": 80, "comment": "很活跃"}],
                "summary": "整体不错",
            }, False)
            result = chatlens_get_chat_quality(group_name="测试群")
        assert '热闹非凡' in result
        assert '活跃度' in result
        assert '整体不错' in result

    def test_empty_quality(self):
        """空结果时 lines 仍包含空字符串 append, 返回空行"""
        msgs = [_make_msg()]
        svc = _make_mock_service(messages=msgs)
        set_service(svc)
        with patch('chatlens.plugins.mcp.mcp_server._ai_or_rule') as mock_ai:
            mock_ai.return_value = ({}, False)
            result = chatlens_get_chat_quality(group_name="测试群")
        # 空结果时 lines 仍有空行 append("")，不会触发 "未能生成"
        assert isinstance(result, str)


# ── chatlens_schedule_create ─────────────────────────────────

class TestChatlensScheduleCreate:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_invalid_group_name(self):
        result = chatlens_schedule_create(group_name="", hour=8, minute=0)
        assert '参数错误' in result

    def test_invalid_time(self):
        result = chatlens_schedule_create(group_name="测试群", hour=25, minute=0)
        assert '参数错误' in result

    def test_create_success(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": True, "task_id": "task123"}
            result = chatlens_schedule_create(group_name="测试群", hour=8, minute=30)
        assert '✅' in result
        assert 'task123' in result

    def test_create_failure(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": False, "error": "服务器错误"}
            result = chatlens_schedule_create(group_name="测试群", hour=8, minute=30)
        assert '❌' in result
        assert '服务器错误' in result


# ── chatlens_schedule_list ───────────────────────────────────

class TestChatlensScheduleList:
    def test_no_tasks(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_get') as mock_get:
            mock_get.return_value = {"tasks": []}
            result = chatlens_schedule_list()
        assert '没有' in result

    def test_with_tasks(self):
        tasks = [{
            "task_id": "t1", "group_name": "群1", "hour": 8, "minute": 30,
            "enabled": True, "status": "idle", "last_run": "2024-01-01",
            "history": [{"success": True, "time": "2024-01-01", "method": "ai"}],
        }]
        with patch('chatlens.plugins.mcp.mcp_server._http_get') as mock_get:
            mock_get.return_value = {"tasks": tasks}
            result = chatlens_schedule_list()
        assert '群1' in result
        assert 't1' in result


# ── chatlens_schedule_trigger ────────────────────────────────

class TestChatlensScheduleTrigger:
    def test_trigger_success(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": True}
            result = chatlens_schedule_trigger(task_id="t1")
        assert '✅' in result

    def test_trigger_failure(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": False, "error": "not found"}
            result = chatlens_schedule_trigger(task_id="t1")
        assert '❌' in result


# ── chatlens_schedule_delete ─────────────────────────────────

class TestChatlensScheduleDelete:
    def test_delete_success(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_delete') as mock_del:
            mock_del.return_value = {"success": True}
            result = chatlens_schedule_delete(task_id="t1")
        assert '✅' in result

    def test_delete_failure(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_delete') as mock_del:
            mock_del.return_value = {"success": False, "error": "not found"}
            result = chatlens_schedule_delete(task_id="t1")
        assert '❌' in result


# ── chatlens_schedule_toggle ─────────────────────────────────

class TestChatlensScheduleToggle:
    def test_enable_success(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": True}
            result = chatlens_schedule_toggle(task_id="t1", enabled=True)
        assert '✅' in result
        assert '启用' in result

    def test_disable_success(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": True}
            result = chatlens_schedule_toggle(task_id="t1", enabled=False)
        assert '✅' in result
        assert '禁用' in result

    def test_toggle_failure(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": False, "error": "fail"}
            result = chatlens_schedule_toggle(task_id="t1")
        assert '❌' in result


# ── chatlens_check_pending ───────────────────────────────────

class TestChatlensCheckPending:
    def test_no_pending(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_get') as mock_get:
            mock_get.return_value = {"tasks": []}
            result = chatlens_check_pending()
        assert '没有' in result

    def test_with_pending(self):
        tasks = [{
            "task_id": "t1", "group_name": "群1", "message_count": 100,
            "theme": "scrapbook", "fmt": "png", "created_at": "2024-01-01",
        }]
        with patch('chatlens.plugins.mcp.mcp_server._http_get') as mock_get:
            mock_get.return_value = {"tasks": tasks}
            result = chatlens_check_pending()
        assert '群1' in result
        assert 't1' in result


# ── chatlens_submit_analysis ─────────────────────────────────

class TestChatlensSubmitAnalysis:
    def test_submit_success(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": True}
            result = chatlens_submit_analysis(task_id="t1", ai_data='{"summary": "测试"}')
        assert '✅' in result

    def test_submit_failure(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": False, "error": "fail"}
            result = chatlens_submit_analysis(task_id="t1")
        assert '❌' in result

    def test_submit_with_legacy_params(self):
        with patch('chatlens.plugins.mcp.mcp_server._http_post') as mock_post:
            mock_post.return_value = {"success": True}
            result = chatlens_submit_analysis(
                task_id="t1", ai_summary="摘要",
                ai_topics='[{"name":"话题"}]',
                ai_user_titles='[{"name":"用户","title":"称号","mbti":"INTJ"}]',
            )
        assert '✅' in result


# ── chatlens_refresh_data ────────────────────────────────────

class TestChatlensRefreshData:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_no_service(self):
        result = chatlens_refresh_data()
        assert '未初始化' in result

    def test_refresh_success(self):
        svc = _make_mock_service()
        set_service(svc)
        with patch('chatlens.plugins.mcp.mcp_server.run_chatlog_decrypt', create=True) as mock_decrypt:
            # Patch the import inside the function
            with patch.dict('sys.modules', {'chatlens.core._chatlog_runtime': MagicMock(run_chatlog_decrypt=MagicMock(return_value=True))}):
                result = chatlens_refresh_data()
        # The function does an import inside, so we test the no-service path mainly
        # and the service path with proper module mocking

    def test_refresh_with_service(self):
        svc = _make_mock_service()
        provider = MagicMock()
        svc.get_provider.return_value = provider
        set_service(svc)
        mock_runtime = MagicMock()
        mock_runtime.run_chatlog_decrypt.return_value = True
        with patch.dict('sys.modules', {'chatlens.core._chatlog_runtime': mock_runtime}):
            result = chatlens_refresh_data()
        assert '✅' in result
        provider.reset_connections.assert_called_once()


# ── chatlog_status ───────────────────────────────────────────

class TestChatlogStatus:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_no_service(self):
        result = chatlog_status()
        assert '未初始化' in result

    def test_no_provider(self):
        svc = _make_mock_service(provider=None)
        set_service(svc)
        result = chatlog_status()
        assert '未配置' in result

    def test_provider_available(self):
        provider = _make_mock_provider(available=True)
        provider.bridge.api_base = "http://localhost:8080"
        provider.bridge.db_path = "/path/to/db"
        svc = _make_mock_service(provider=provider)
        set_service(svc)
        result = chatlog_status()
        assert '是' in result
        assert 'localhost' in result

    def test_provider_not_available(self):
        provider = _make_mock_provider(available=False)
        svc = _make_mock_service(provider=provider)
        set_service(svc)
        result = chatlog_status()
        assert '否' in result


# ── setup ────────────────────────────────────────────────────

class TestSetup:
    def setup_method(self):
        set_service(None)

    def teardown_method(self):
        set_service(None)

    def test_setup_creates_service(self):
        ga = MagicMock()
        setup(ga)
        svc = get_service()
        assert svc is not None
        assert isinstance(svc, MCPService)
        assert svc.ga is ga

    def test_setup_sets_ga_mcp(self):
        ga = MagicMock()
        setup(ga)
        assert ga.mcp is get_service()


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

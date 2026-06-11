"""分析工具函数单元测试 — _analysis_utils.py"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from chatlens.core.models import ChatMessage
from chatlens.core._analysis_utils import (
    build_fallback_vibe,
    format_messages_id_only,
    build_id_map_text,
    replace_ids_in_string,
    map_ids_back,
    inject_personality,
    try_regex_extract,
    parse_json_response,
    get_user_stats,
)


def _msg(sender='Alice', content='Hello world', msg_type='text', msg_attr='',
         timestamp='2026-05-01 10:00:00', group_name='TestGroup', sender_remark=''):
    return ChatMessage(sender=sender, content=content, msg_type=msg_type,
                       msg_attr=msg_attr, timestamp=timestamp,
                       group_name=group_name, sender_remark=sender_remark)


# ── build_fallback_vibe ──────────────────────────────────────

class TestBuildFallbackVibe:
    def test_basic_dims(self):
        dims = build_fallback_vibe(50, 10, 5, 3, 2)
        assert len(dims) >= 4
        names = [d['name'] for d in dims]
        assert any('闲聊' in n for n in names)
        assert any('图片' in n for n in names)

    def test_other_pct_shows_extra_dim(self):
        dims = build_fallback_vibe(40, 10, 5, 3, 10)
        assert len(dims) == 5
        assert any('其他' in d['name'] for d in dims)

    def test_no_other_when_low(self):
        dims = build_fallback_vibe(80, 5, 3, 2, 1)
        assert all('其他' not in d['name'] for d in dims)

    def test_percentage_capped(self):
        dims = build_fallback_vibe(90, 50, 30, 20, 10)
        for d in dims:
            assert d['percentage'] <= 60 or '其他' in d['name']


# ── format_messages_id_only ──────────────────────────────────

class TestFormatMessagesIdOnly:
    def test_basic_formatting(self):
        msgs = [_msg(sender='Alice', content='Hi'), _msg(sender='Bob', content='Hey')]
        text, id_map = format_messages_id_only(msgs)
        assert 'U1' in text
        assert 'U2' in text
        assert 'Hi' in text
        assert len(id_map) == 2

    def test_sender_remark_preferred(self):
        msgs = [_msg(sender='wxid_abc', sender_remark='小明', content='Hello')]
        text, id_map = format_messages_id_only(msgs)
        assert '小明' in id_map

    def test_system_messages_skipped(self):
        msgs = [_msg(msg_attr='system', content='joined'), _msg(content='Hello')]
        text, id_map = format_messages_id_only(msgs)
        assert 'joined' not in text
        assert len(id_map) == 1

    def test_max_messages_limit(self):
        msgs = [_msg(content=f'msg{i}') for i in range(300)]
        text, _ = format_messages_id_only(msgs, max_messages=50)
        lines = [l for l in text.split('\n') if l.strip()]
        assert len(lines) <= 50

    def test_image_type_format(self):
        msgs = [_msg(msg_type='image', content='')]
        text, _ = format_messages_id_only(msgs)
        assert '[图片]' in text

    def test_quote_type_format(self):
        msgs = [_msg(msg_type='quote', content='reply')]
        # quote_content defaults to empty string
        text, _ = format_messages_id_only(msgs)
        assert '引用' in text


# ── build_id_map_text ────────────────────────────────────────

class TestBuildIdMapText:
    def test_basic(self):
        id_map = {'Alice': 'U1', 'Bob': 'U2'}
        text = build_id_map_text(id_map)
        assert 'U1 = Alice' in text
        assert 'U2 = Bob' in text

    def test_sorted_by_id_number(self):
        id_map = {'Zoe': 'U3', 'Alice': 'U1', 'Bob': 'U2'}
        text = build_id_map_text(id_map)
        lines = text.strip().split('\n')
        assert lines[0] == 'U1 = Alice'


# ── replace_ids_in_string ────────────────────────────────────

class TestReplaceIdsInString:
    def test_basic_replacement(self):
        id_map = {'Alice': 'U1', 'Bob': 'U2'}
        result = replace_ids_in_string('U1 said hello to U2', id_map)
        assert result == 'Alice said hello to Bob'

    def test_no_match(self):
        id_map = {'Alice': 'U1'}
        result = replace_ids_in_string('U99 is unknown', id_map)
        assert result == 'U99 is unknown'


# ── map_ids_back ─────────────────────────────────────────────

class TestMapIdsBack:
    def test_dict_mapping(self):
        id_map = {'Alice': 'U1'}
        data = {'user': 'U1 is great', 'count': 5}
        result = map_ids_back(data, id_map)
        assert result['user'] == 'Alice is great'
        assert result['count'] == 5

    def test_nested_mapping(self):
        id_map = {'Alice': 'U1'}
        data = {'users': [{'name': 'U1'}]}
        result = map_ids_back(data, id_map)
        assert result['users'][0]['name'] == 'Alice'

    def test_list_mapping(self):
        id_map = {'Alice': 'U1'}
        data = ['U1 hello', 'no match']
        result = map_ids_back(data, id_map)
        assert result[0] == 'Alice hello'
        assert result[1] == 'no match'

    def test_non_string_passthrough(self):
        id_map = {'Alice': 'U1'}
        assert map_ids_back(42, id_map) == 42
        assert map_ids_back(None, id_map) is None


# ── inject_personality ───────────────────────────────────────

class TestInjectPersonality:
    def test_adds_head_and_tail(self):
        result = inject_personality('Return JSON output', 'summary')
        assert len(result) > len('Return JSON output')
        # Should contain personality markers
        assert 'JSON' in result

    def test_unknown_type_uses_default(self):
        result = inject_personality('Return JSON', 'nonexistent_type')
        assert 'JSON' in result


# ── try_regex_extract ────────────────────────────────────────

class TestTryRegexExtract:
    def test_extract_string_field(self):
        text = '{"summary": "hello world"}'
        schema = {'summary': ''}
        result = try_regex_extract(text, schema)
        assert result is not None
        assert result['summary'] == 'hello world'

    def test_extract_number_field(self):
        text = '{"count": 42}'
        schema = {'count': 0}
        result = try_regex_extract(text, schema)
        assert result is not None
        assert result['count'] == 42

    def test_extract_list_field(self):
        text = '{"items": ["a", "b"]}'
        schema = {'items': []}
        result = try_regex_extract(text, schema)
        assert result is not None
        assert result['items'] == ['a', 'b']

    def test_no_match_returns_default(self):
        text = 'no json here'
        schema = {'name': 'default'}
        result = try_regex_extract(text, schema)
        assert result['name'] == 'default'


# ── parse_json_response ──────────────────────────────────────

class TestParseJsonResponse:
    def test_valid_json(self):
        result = parse_json_response('{"key": "value"}')
        assert result == {'key': 'value'}

    def test_json_in_code_block(self):
        result = parse_json_response('```json\n{"key": "value"}\n```')
        assert result == {'key': 'value'}

    def test_json_with_prefix(self):
        result = parse_json_response('Here is the result: {"key": "value"}')
        assert result == {'key': 'value'}

    def test_empty_input(self):
        assert parse_json_response('') is None
        assert parse_json_response(None) is None

    def test_invalid_json_falls_back_to_regex(self):
        result = parse_json_response('{"summary": "test"}', schema={'summary': ''})
        assert result is not None
        assert result['summary'] == 'test'

    def test_list_json_returned_as_is(self):
        result = parse_json_response('[{"a": 1}, {"a": 2}]')
        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 2


# ── get_user_stats ───────────────────────────────────────────

class TestGetUserStats:
    def test_basic_stats(self):
        msgs = [_msg(sender='Alice', content='Hello'), _msg(sender='Bob', content='Hi there')]
        stats = get_user_stats(msgs)
        assert 'Alice' in stats
        assert 'Bob' in stats
        assert stats['Alice']['message_count'] == 1
        assert stats['Bob']['message_count'] == 1

    def test_char_count(self):
        msgs = [_msg(sender='Alice', content='Hello world!')]
        stats = get_user_stats(msgs)
        assert stats['Alice']['char_count'] == 12

    def test_image_count(self):
        msgs = [_msg(sender='Alice', msg_type='image')]
        stats = get_user_stats(msgs)
        assert stats['Alice']['image_count'] == 1

    def test_night_count(self):
        msgs = [_msg(sender='Alice', timestamp='2026-05-01 03:00:00')]
        stats = get_user_stats(msgs)
        assert stats['Alice']['night_count'] == 1

    def test_system_messages_excluded(self):
        msgs = [_msg(msg_attr='system', sender='System')]
        stats = get_user_stats(msgs)
        assert 'System' not in stats

    def test_sender_remark_used(self):
        msgs = [_msg(sender='wxid_abc', sender_remark='小明')]
        stats = get_user_stats(msgs)
        assert '小明' in stats
        assert 'wxid_abc' not in stats

    def test_multiple_messages(self):
        msgs = [_msg(sender='Alice', content='Hi')] * 5
        stats = get_user_stats(msgs)
        assert stats['Alice']['message_count'] == 5

    def test_empty_messages(self):
        stats = get_user_stats([])
        assert stats == {}


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

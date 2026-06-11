"""ChatlogBridge 单元测试 — 使用内存 sqlite3 数据库 + mock"""

import os
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from chatlens.core.chatlog_bridge import (
    ChatlogBridge,
    _validate_table_name,
    _talker_to_table_name,
    _try_zstd_decompress,
    MSG_TYPE_MAP,
    MSG_SUB_TYPE_MAP,
    ZSTD_MAGIC,
)
from chatlens.core.models import ChatMessage


# ---------------------------------------------------------------------------
# 辅助：创建内存消息数据库（含 Name2Id + 一个聊天表）
# ---------------------------------------------------------------------------
def _make_msg_db(talker: str = "test_user", rows=None, name2id_rows=None):
    """返回内存 sqlite3 数据库，模拟 chatlog 消息库结构"""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS Name2Id (rowid INTEGER PRIMARY KEY, user_name TEXT, is_session INTEGER)"
    )
    for item in name2id_rows or []:
        db.execute("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (?, ?, ?)", item)

    table_name = _talker_to_table_name(talker)
    db.execute(
        f"CREATE TABLE IF NOT EXISTS [{table_name}] ("
        "local_id INTEGER, sort_seq INTEGER, server_id INTEGER, "
        "local_type INTEGER, real_sender_id INTEGER, "
        "create_time INTEGER, message_content TEXT, status INTEGER)"
    )
    for r in rows or []:
        db.execute(
            f"INSERT INTO [{table_name}] VALUES (?, ?, ?, ?, ?, ?, ?, ?)", r
        )
    db.commit()
    return db


def _make_contact_db(contacts=None):
    """返回内存 sqlite3 数据库，模拟 chatlog 联系人库"""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS contact (username TEXT, nick_name TEXT, remark TEXT, alias TEXT)"
    )
    for c in contacts or []:
        db.execute("INSERT INTO contact VALUES (?, ?, ?, ?)", c)
    db.commit()
    return db


# ========================== 模块级函数测试 ==========================

class TestValidateTableName(unittest.TestCase):
    def test_valid_names(self):
        # 合法表名不应抛异常
        _validate_table_name("Msg_abc123")
        _validate_table_name("_underscore")
        _validate_table_name("A")

    def test_invalid_names_with_special_chars(self):
        for bad in ["Msg; DROP TABLE", "table-name", "table.name", "table name", ""]:
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    _validate_table_name(bad)

    def test_sql_injection(self):
        with self.assertRaises(ValueError):
            _validate_table_name("Msg; DROP TABLE Name2Id;--")
        with self.assertRaises(ValueError):
            _validate_table_name("1 OR 1=1")


class TestTalkerToTableName(unittest.TestCase):
    def test_deterministic(self):
        name1 = _talker_to_table_name("user@chatroom")
        name2 = _talker_to_table_name("user@chatroom")
        self.assertEqual(name1, name2)
        self.assertTrue(name1.startswith("Msg_"))

    def test_different_talkers_different_tables(self):
        self.assertNotEqual(
            _talker_to_table_name("a@chatroom"),
            _talker_to_table_name("b@chatroom"),
        )


class TestTryZstdDecompress(unittest.TestCase):
    def test_plain_bytes(self):
        data = "hello world".encode("utf-8")
        self.assertEqual(_try_zstd_decompress(data), "hello world")

    def test_zstd_magic_prefix_invalid_data(self):
        # 以 zstd 魔数开头但数据无效，mock 模块级单例的 decompress 抛出异常
        data = ZSTD_MAGIC + b"\x00\x01\x02"
        from chatlens.core import chatlog_bridge

        class _FakeDecompressor:
            def decompress(self, _data):
                raise OSError("bad data")

        with patch.object(chatlog_bridge, '_zstd_d', _FakeDecompressor()):
            result = _try_zstd_decompress(data)
        self.assertEqual(result, '')


# ========================== ChatlogBridge 测试 ==========================

class TestChatlogBridgeInit(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_default_api_base(self, mock_ensure):
        bridge = ChatlogBridge()
        self.assertEqual(bridge.api_base, "http://localhost:5030")

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_custom_api_base(self, mock_ensure):
        bridge = ChatlogBridge(api_base="http://custom:9999/")
        self.assertEqual(bridge.api_base, "http://custom:9999")

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_empty_api_base_uses_default(self, mock_ensure):
        bridge = ChatlogBridge(api_base="")
        self.assertEqual(bridge.api_base, "http://localhost:5030")

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_db_path_stored(self, mock_ensure):
        bridge = ChatlogBridge(db_path="/tmp/test.db")
        self.assertEqual(bridge.db_path, "/tmp/test.db")


class TestExtractSubType(unittest.TestCase):
    def test_type_57_quote(self):
        xml = '<appmsg type="57"><title>引用消息</title></appmsg>'
        self.assertEqual(ChatlogBridge._extract_sub_type(xml), 57)

    def test_type_6_file(self):
        xml = '<appmsg type="6"><title>文件</title></appmsg>'
        self.assertEqual(ChatlogBridge._extract_sub_type(xml), 6)

    def test_no_appmsg_tag(self):
        self.assertEqual(ChatlogBridge._extract_sub_type("plain text"), 0)

    def test_empty_content(self):
        self.assertEqual(ChatlogBridge._extract_sub_type(""), 0)
        self.assertEqual(ChatlogBridge._extract_sub_type(None), 0)

    def test_bytes_content(self):
        xml = '<appmsg type="19"><title>合并转发</title></appmsg>'.encode("utf-8")
        self.assertEqual(ChatlogBridge._extract_sub_type(xml), 19)

    def test_appmsg_without_type_attr(self):
        xml = '<appmsg><title>no type</title></appmsg>'
        self.assertEqual(ChatlogBridge._extract_sub_type(xml), 0)


class TestGetAllTalkers(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def setUp(self, mock_ensure):
        self.bridge = ChatlogBridge(db_path=None)
        self.bridge._msg_db = None
        self.bridge._contact_db = None

    def test_returns_list_when_no_db(self):
        with patch.object(self.bridge, '_get_msg_db', return_value=None):
            result = self.bridge.get_all_talkers()
            self.assertEqual(result, [])

    def test_returns_talkers_from_db(self):
        talker1 = "user1@chatroom"
        talker2 = "user2"
        msg_db = _make_msg_db(
            name2id_rows=[
                (1, talker1, 1),
                (2, talker2, 0),
            ]
        )
        # 为每个 talker 创建对应的消息表
        for t in [talker1, talker2]:
            tn = _talker_to_table_name(t)
            msg_db.execute(f"CREATE TABLE IF NOT EXISTS [{tn}] (local_id INTEGER)")
            msg_db.execute(f"INSERT INTO [{tn}] VALUES (1)")

        msg_db.commit()

        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {
                talker1: {'nick_name': '群聊1', 'remark': '', 'alias': ''},
                talker2: {'nick_name': '用户2', 'remark': '备注2', 'alias': ''},
            }
            result = self.bridge.get_all_talkers()

        self.assertEqual(len(result), 2)
        # 结果按 message_count 降序排列
        self.assertEqual(result[0]['talker'], talker1)
        self.assertTrue(result[0]['is_chatroom'])
        self.assertFalse(result[1]['is_chatroom'])
        self.assertEqual(result[1]['display_name'], '备注2')

    def test_skips_empty_username(self):
        msg_db = _make_msg_db(
            name2id_rows=[
                (1, "", 0),
                (2, "valid_user", 1),
            ]
        )
        tn = _talker_to_table_name("valid_user")
        msg_db.execute(f"CREATE TABLE IF NOT EXISTS [{tn}] (local_id INTEGER)")
        msg_db.commit()

        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_all_talkers()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['talker'], 'valid_user')


class TestGetMessages(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def setUp(self, mock_ensure):
        self.bridge = ChatlogBridge(db_path=None)
        self.bridge._msg_db = None
        self.bridge._contact_db = None

    def test_returns_empty_when_no_db(self):
        with patch.object(self.bridge, '_get_msg_db', return_value=None):
            result = self.bridge.get_messages("test@chatroom")
            self.assertEqual(result, [])

    def test_text_message_parsing(self):
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 1, 1, 1700000000, "你好", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {talker: {'nick_name': '好友1', 'remark': '', 'alias': ''}}
            result = self.bridge.get_messages(talker)

        self.assertEqual(len(result), 1)
        msg = result[0]
        self.assertIsInstance(msg, ChatMessage)
        self.assertEqual(msg.msg_type, 'text')
        self.assertEqual(msg.content, '你好')
        self.assertEqual(msg.msg_attr, 'friend')

    def test_image_message(self):
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 3, 1, 1700000000, "<img/>", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].msg_type, 'image')
        # image 消息 content 应为空
        self.assertEqual(result[0].content, '')

    def test_quote_message_sub_type_57(self):
        """sub_type=57 的 type=49 消息应被识别为 quote，不被覆盖为 link"""
        talker = "friend1"
        xml = '<appmsg type="57"><title>引用</title></appmsg>'
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 49, 1, 1700000000, xml, 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].msg_type, 'quote')
        # quote 消息应有 quote_content
        self.assertTrue(len(result[0].quote_content) > 0)

    def test_link_message_type_49_sub_type_5(self):
        talker = "friend1"
        xml = '<appmsg type="5"><title>链接</title></appmsg>'
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 49, 1, 1700000000, xml, 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].msg_type, 'link')

    def test_system_message_filter(self):
        """type=10000 和 10002 应被识别为 system"""
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 10000, 1, 1700000000, "你已添加好友", 0),
                (2, 2, 101, 10002, 1, 1700000000, "撤回了一条消息", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].msg_type, 'system')
        self.assertEqual(result[0].msg_attr, 'system')
        self.assertEqual(result[1].msg_type, 'system')
        self.assertEqual(result[1].msg_attr, 'system')

    def test_self_message_status_2(self):
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 1, 1, 1700000000, "我发的", 2),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].msg_attr, 'self')

    def test_chatroom_message_sender_extraction(self):
        """群聊消息应从 content 中提取 sender"""
        talker = "group@chatroom"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, "sender_wxid", 0)],
            rows=[
                (1, 1, 100, 1, 1, 1700000000, "sender_wxid:\n群聊消息内容", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {"sender_wxid": {'nick_name': '发送者', 'remark': '', 'alias': ''}}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].content, '群聊消息内容')
        self.assertEqual(result[0].sender, '发送者')

    def test_limit_parameter(self):
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 1, 1, 1700000000, "msg1", 0),
                (2, 2, 101, 1, 1, 1700000001, "msg2", 0),
                (3, 3, 102, 1, 1, 1700000002, "msg3", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker, limit=2)

        self.assertEqual(len(result), 2)

    def test_nonexistent_table(self):
        """talker 对应的表不存在时应返回空列表"""
        talker = "nonexistent_user"
        msg_db = _make_msg_db(name2id_rows=[])
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            result = self.bridge.get_messages(talker)

        self.assertEqual(result, [])

    def test_timestamp_formatting(self):
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 1, 1, 1700000000, "test", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertRegex(result[0].timestamp, r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')

    def test_type_49_non_quote_with_appmsg_tag_becomes_link(self):
        """type=49 且 sub_type 不是 quote，但内容含 <appmsg 标签，应被覆盖为 link"""
        talker = "friend1"
        # sub_type=4 对应 link，且内容含 <appmsg 标签
        xml = '<appmsg type="4"><title>文件</title></appmsg>'
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 49, 1, 1700000000, xml, 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].msg_type, 'link')


class TestLoadContacts(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def setUp(self, mock_ensure):
        self.bridge = ChatlogBridge(db_path=None)
        self.bridge._msg_db = None
        self.bridge._contact_db = None

    def test_loads_contacts_into_cache(self):
        contact_db = _make_contact_db([
            ("user1", "昵称1", "备注1", "alias1"),
            ("user2", "昵称2", "", "alias2"),
        ])
        with patch.object(self.bridge, '_get_contact_db', return_value=contact_db), \
             patch.object(self.bridge, '_load_session_names'):
            self.bridge._contact_cache = {}
            self.bridge._contact_cache_time = 0
            self.bridge._load_contacts()

        self.assertIn("user1", self.bridge._contact_cache)
        self.assertEqual(self.bridge._contact_cache["user1"]["remark"], "备注1")
        self.assertIn("user2", self.bridge._contact_cache)
        self.assertEqual(self.bridge._contact_cache["user2"]["nick_name"], "昵称2")

    def test_cache_ttl_skips_reload(self):
        """缓存未过期时不应重新加载"""
        self.bridge._contact_cache = {"cached_user": {'nick_name': '缓存', 'remark': '', 'alias': ''}}
        self.bridge._contact_cache_time = time.time()  # 刚加载

        with patch.object(self.bridge, '_get_contact_db') as mock_get_db:
            self.bridge._load_contacts()
            mock_get_db.assert_not_called()

    def test_cache_expired_reloads(self):
        """缓存过期后应重新加载"""
        self.bridge._contact_cache = {"old_user": {'nick_name': '旧', 'remark': '', 'alias': ''}}
        self.bridge._contact_cache_time = time.time() - 601  # 超过 TTL

        contact_db = _make_contact_db([
            ("new_user", "新用户", "", ""),
        ])
        with patch.object(self.bridge, '_get_contact_db', return_value=contact_db), \
             patch.object(self.bridge, '_load_session_names'):
            self.bridge._load_contacts()

        self.assertNotIn("old_user", self.bridge._contact_cache)
        self.assertIn("new_user", self.bridge._contact_cache)

    def test_no_contact_db_returns_early(self):
        with patch.object(self.bridge, '_get_contact_db', return_value=None):
            self.bridge._contact_cache = {}
            self.bridge._contact_cache_time = 0
            self.bridge._load_contacts()

        self.assertEqual(self.bridge._contact_cache, {})

    def test_null_fields_replaced_with_empty_string(self):
        contact_db = sqlite3.connect(":memory:")
        contact_db.row_factory = sqlite3.Row
        contact_db.execute("CREATE TABLE contact (username TEXT, nick_name TEXT, remark TEXT, alias TEXT)")
        contact_db.execute("INSERT INTO contact VALUES (?, ?, ?, ?)", ("user1", None, None, None))
        contact_db.commit()

        with patch.object(self.bridge, '_get_contact_db', return_value=contact_db), \
             patch.object(self.bridge, '_load_session_names'):
            self.bridge._contact_cache = {}
            self.bridge._contact_cache_time = 0
            self.bridge._load_contacts()

        self.assertEqual(self.bridge._contact_cache["user1"]["nick_name"], "")
        self.assertEqual(self.bridge._contact_cache["user1"]["remark"], "")


class TestGetDisplayName(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def setUp(self, mock_ensure):
        self.bridge = ChatlogBridge(db_path=None)
        self.bridge._msg_db = None
        self.bridge._contact_db = None

    def test_remark_takes_priority(self):
        self.bridge._contact_cache = {
            "user1": {'nick_name': '昵称', 'remark': '备注', 'alias': ''},
        }
        self.bridge._contact_cache_time = time.time()
        with patch.object(self.bridge, '_load_contacts'):
            self.assertEqual(self.bridge._get_display_name("user1"), "备注")

    def test_nick_name_as_fallback(self):
        self.bridge._contact_cache = {
            "user2": {'nick_name': '昵称', 'remark': '', 'alias': ''},
        }
        self.bridge._contact_cache_time = time.time()
        with patch.object(self.bridge, '_load_contacts'):
            self.assertEqual(self.bridge._get_display_name("user2"), "昵称")

    def test_username_as_last_resort(self):
        self.bridge._contact_cache = {}
        self.bridge._contact_cache_time = time.time()
        with patch.object(self.bridge, '_load_contacts'):
            self.assertEqual(self.bridge._get_display_name("unknown_user"), "unknown_user")


class TestResetConnections(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_resets_all_connections_and_cache(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        mock_msg_db = MagicMock()
        bridge._msg_db = mock_msg_db
        bridge._contact_db = MagicMock()
        bridge._contact_cache = {"user": {'nick_name': 'test', 'remark': '', 'alias': ''}}
        bridge._contact_cache_time = 12345.0

        bridge.reset_connections()

        mock_msg_db.close.assert_called_once()
        self.assertIsNone(bridge._msg_db)
        self.assertIsNone(bridge._contact_db)
        self.assertEqual(bridge._contact_cache, {})
        self.assertEqual(bridge._contact_cache_time, 0)


class TestClose(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_close_resets_connections(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        mock_msg_db = MagicMock()
        bridge._msg_db = mock_msg_db
        bridge._contact_db = MagicMock()
        bridge._contact_cache = {"user": {'nick_name': 'test', 'remark': '', 'alias': ''}}

        bridge.reset_connections()

        mock_msg_db.close.assert_called_once()
        self.assertIsNone(bridge._msg_db)
        self.assertIsNone(bridge._contact_db)


class TestIsAvailable(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_available_when_db_exists(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        with patch.object(bridge, '_get_msg_db', return_value=MagicMock()):
            self.assertTrue(bridge.is_available())

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_not_available_when_no_db(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        with patch.object(bridge, '_get_msg_db', return_value=None):
            self.assertFalse(bridge.is_available())


class TestGetChatrooms(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_filters_chatrooms(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        bridge._contact_cache = {
            "room1@chatroom": {'nick_name': '群1', 'remark': '', 'alias': ''},
            "user1": {'nick_name': '用户1', 'remark': '', 'alias': ''},
            "room2@chatroom": {'nick_name': '', 'remark': '群2备注', 'alias': ''},
        }
        bridge._contact_cache_time = time.time()

        with patch.object(bridge, '_load_contacts'):
            result = bridge.get_chatrooms()

        self.assertEqual(len(result), 2)
        names = [r['name'] for r in result]
        self.assertIn("room1@chatroom", names)
        self.assertIn("room2@chatroom", names)
        self.assertNotIn("user1", names)

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_chatroom_display_name_priority(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        bridge._contact_cache = {
            "room@chatroom": {'nick_name': '群昵称', 'remark': '群备注', 'alias': ''},
        }
        bridge._contact_cache_time = time.time()

        with patch.object(bridge, '_load_contacts'):
            result = bridge.get_chatrooms()

        # remark 优先
        self.assertEqual(result[0]['nickName'], '群备注')


class TestEnsureSchemaVersion(unittest.TestCase):
    def test_creates_meta_table_and_sets_version(self):
        """使用内存数据库验证 schema 版本写入"""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row

        with patch.object(ChatlogBridge, '_ensure_schema_version', lambda self: None):
            bridge = ChatlogBridge(db_path=None)

        # 手动调用 _ensure_schema_version，mock _get_msg_db
        with patch.object(bridge, '_get_msg_db', return_value=db):
            bridge._ensure_schema_version()

        cursor = db.execute("SELECT value FROM _meta WHERE key='schema_version'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(int(row['value']), ChatlogBridge.CURRENT_SCHEMA_VERSION)


class TestGetMsgDb(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_returns_existing_connection_if_alive(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        mock_db = MagicMock()
        mock_db.execute.return_value = MagicMock()  # SELECT 1 works
        bridge._msg_db = mock_db

        result = bridge._get_msg_db()
        self.assertIs(result, mock_db)

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_reconnects_when_connection_broken(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.Error("connection closed")
        bridge._msg_db = mock_db

        # _find_db_dir 返回 None，所以最终返回 None
        with patch.object(bridge, '_find_db_dir', return_value=None):
            result = bridge._get_msg_db()

        self.assertIsNone(result)
        self.assertIsNone(bridge._msg_db)


class TestApiGet(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_api_get_returns_parsed_json(self, mock_ensure):
        bridge = ChatlogBridge(api_base="http://localhost:5030")
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "ok"}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('urllib.request.urlopen', return_value=mock_response):
            result = bridge._api_get("/api/test")

        self.assertEqual(result, {"status": "ok"})

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_api_get_returns_none_on_error(self, mock_ensure):
        bridge = ChatlogBridge(api_base="http://localhost:5030")
        import urllib.error
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError("fail")):
            result = bridge._api_get("/api/test")

        self.assertIsNone(result)


class TestFindDbDir(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_returns_none_when_no_dirs_exist(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        with patch('os.path.exists', return_value=False):
            result = bridge._find_db_dir()
        self.assertIsNone(result)

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_returns_dir_with_newest_message_db(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        call_count = [0]

        def mock_exists(path):
            # 只有 db_storage/message/message_0.db 存在
            return path.endswith('message_0.db') or path.endswith('db_storage')

        def mock_getmtime(path):
            return 1700000000.0

        with patch('os.path.exists', side_effect=mock_exists), \
             patch('os.path.getmtime', side_effect=mock_getmtime):
            result = bridge._find_db_dir()
        # 返回包含 message_0.db 的目录
        self.assertIsNotNone(result)
        self.assertIn('db_storage', result)

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_returns_existing_dir_as_fallback(self, mock_ensure):
        """当没有 message_0.db 时，返回存在的 db_storage 目录"""
        bridge = ChatlogBridge(db_path=None)

        def mock_exists(path):
            # message_0.db 不存在，但 db_storage 目录存在
            return path.endswith('db_storage') and not path.endswith('message_0.db')

        with patch('os.path.exists', side_effect=mock_exists):
            result = bridge._find_db_dir()
        self.assertIsNotNone(result)


class TestGetMsgDbFullPaths(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_connects_to_db_path_when_exists(self, mock_ensure):
        """db_path 存在时应直接连接"""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            tmp_path = f.name
            # 创建一个简单的 sqlite3 文件
            conn = sqlite3.connect(tmp_path)
            conn.execute("CREATE TABLE test (id INTEGER)")
            conn.commit()
            conn.close()

        try:
            with patch.object(ChatlogBridge, '_ensure_schema_version', lambda self: None):
                bridge = ChatlogBridge(db_path=tmp_path)
            result = bridge._get_msg_db()
            self.assertIsNotNone(result)
            self.assertIsNotNone(bridge._msg_db)
            # 先关闭连接再删文件
            if bridge._msg_db:
                bridge._msg_db.close()
                bridge._msg_db = None
        finally:
            try:
                os.unlink(tmp_path)
            except PermissionError:
                pass

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_returns_none_when_db_path_not_exists(self, mock_ensure):
        bridge = ChatlogBridge(db_path="/nonexistent/path.db")
        with patch.object(bridge, '_find_db_dir', return_value=None):
            result = bridge._get_msg_db()
        self.assertIsNone(result)

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_connects_via_find_db_dir(self, mock_ensure):
        """当 db_path 不可用时，通过 _find_db_dir 查找"""
        tmpdir = tempfile.mkdtemp()
        try:
            msg_dir = os.path.join(tmpdir, 'message')
            os.makedirs(msg_dir)
            db_path = os.path.join(msg_dir, 'message_0.db')
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE test (id INTEGER)")
            conn.commit()
            conn.close()

            with patch.object(ChatlogBridge, '_ensure_schema_version', lambda self: None):
                bridge = ChatlogBridge(db_path=None)
                bridge._msg_db = None
            with patch.object(bridge, '_find_db_dir', return_value=tmpdir):
                result = bridge._get_msg_db()
            self.assertIsNotNone(result)
            # 先关闭连接
            if bridge._msg_db:
                bridge._msg_db.close()
                bridge._msg_db = None
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except PermissionError:
                pass


class TestGetContactDb(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_returns_none_when_no_db_dir(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        bridge._contact_db = None
        with patch.object(bridge, '_find_db_dir', return_value=None):
            result = bridge._get_contact_db()
        self.assertIsNone(result)

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_returns_none_when_contact_db_not_exists(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        bridge._contact_db = None
        tmpdir = tempfile.mkdtemp()
        try:
            with patch.object(bridge, '_find_db_dir', return_value=tmpdir):
                # contact 子目录不存在
                result = bridge._get_contact_db()
            self.assertIsNone(result)
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except PermissionError:
                pass

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_connects_to_contact_db(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        bridge._contact_db = None
        tmpdir = tempfile.mkdtemp()
        try:
            contact_dir = os.path.join(tmpdir, 'contact')
            os.makedirs(contact_dir)
            db_path = os.path.join(contact_dir, 'contact.db')
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE contact (username TEXT)")
            conn.commit()
            conn.close()

            with patch.object(bridge, '_find_db_dir', return_value=tmpdir):
                result = bridge._get_contact_db()
            self.assertIsNotNone(result)
            self.assertIsNotNone(bridge._contact_db)
            # 关闭连接
            if bridge._contact_db:
                bridge._contact_db.close()
                bridge._contact_db = None
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except PermissionError:
                pass

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_reuses_existing_connection(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        mock_db = MagicMock()
        mock_db.execute.return_value = MagicMock()
        bridge._contact_db = mock_db

        result = bridge._get_contact_db()
        self.assertIs(result, mock_db)

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_reconnects_when_connection_broken(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.Error("broken")
        bridge._contact_db = mock_db

        with patch.object(bridge, '_find_db_dir', return_value=None):
            result = bridge._get_contact_db()
        self.assertIsNone(result)
        self.assertIsNone(bridge._contact_db)


class TestLoadSessionNames(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_no_db_dir_returns_early(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        with patch.object(bridge, '_find_db_dir', return_value=None):
            bridge._load_session_names()
        # 不应抛异常

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_no_session_db_returns_early(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        tmpdir = tempfile.mkdtemp()
        try:
            with patch.object(bridge, '_find_db_dir', return_value=tmpdir):
                bridge._load_session_names()
            # session.db 不存在，不应抛异常
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except PermissionError:
                pass

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_loads_session_names_into_cache(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        bridge._contact_cache = {
            "room@chatroom": {'nick_name': '', 'remark': '', 'alias': ''},
        }
        tmpdir = tempfile.mkdtemp()
        try:
            session_dir = os.path.join(tmpdir, 'session')
            os.makedirs(session_dir)
            db_path = os.path.join(session_dir, 'session.db')
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE SessionNoContactInfoTable (username TEXT, session_title TEXT)"
            )
            conn.execute(
                "INSERT INTO SessionNoContactInfoTable VALUES (?, ?)",
                ("room@chatroom", "群名称"),
            )
            conn.execute(
                "INSERT INTO SessionNoContactInfoTable VALUES (?, ?)",
                ("new_room@chatroom", "新群"),
            )
            conn.execute(
                "INSERT INTO SessionNoContactInfoTable VALUES (?, ?)",
                ("empty_title", ""),
            )
            conn.execute(
                "INSERT INTO SessionNoContactInfoTable VALUES (?, ?)",
                ("null_title", None),
            )
            conn.commit()
            conn.close()

            with patch.object(bridge, '_find_db_dir', return_value=tmpdir):
                bridge._load_session_names()

            # 已有联系人应更新 nick_name
            self.assertEqual(bridge._contact_cache["room@chatroom"]["nick_name"], "群名称")
            # 新联系人应被添加
            self.assertIn("new_room@chatroom", bridge._contact_cache)
            self.assertEqual(bridge._contact_cache["new_room@chatroom"]["nick_name"], "新群")
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except PermissionError:
                pass

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_concurrent_load_session_names_no_programming_error(self, mock_ensure):
        """Bug3 修复：多线程并发调用 _load_session_names 不应抛 ProgrammingError。

        原实现 sdb = sqlite3.connect(session_path) 用了默认的 check_same_thread=True，
        当连接在不同线程被使用时（包括 connection 对象在线程间传递、迭代游标
        跨线程、或连接关闭时检测到非创建线程访问）会触发 sqlite3.ProgrammingError。
        """
        bridge = ChatlogBridge(db_path=None)
        bridge._contact_cache = {}
        tmpdir = tempfile.mkdtemp()
        try:
            session_dir = os.path.join(tmpdir, 'session')
            os.makedirs(session_dir)
            db_path = os.path.join(session_dir, 'session.db')
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE SessionNoContactInfoTable (username TEXT, session_title TEXT)"
            )
            for i in range(20):
                conn.execute(
                    "INSERT INTO SessionNoContactInfoTable VALUES (?, ?)",
                    (f"room{i}@chatroom", f"群{i}"),
                )
            conn.commit()
            conn.close()

            n_threads = 8
            barrier = threading.Barrier(n_threads)
            errors = []

            def worker():
                try:
                    # 等所有线程都就绪再并发触发，最大化交叉访问概率
                    barrier.wait(timeout=5)
                    with patch.object(bridge, '_find_db_dir', return_value=tmpdir):
                        for _ in range(3):
                            bridge._load_session_names()
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
                self.assertFalse(t.is_alive(), "线程超时未结束")

            # 关键断言：多线程并发不能抛 ProgrammingError
            programming_errors = [e for e in errors if isinstance(e, sqlite3.ProgrammingError)]
            self.assertEqual(
                programming_errors,
                [],
                f"多线程 _load_session_names 出现 ProgrammingError: "
                f"{[repr(e) for e in programming_errors]}",
            )
            # 同时不应该有其它未捕获异常
            self.assertEqual(errors, [], f"多线程 _load_session_names 出现异常: {errors}")
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except PermissionError:
                pass


class TestLoadContactsErrorHandling(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_db_error_during_load(self, mock_ensure):
        """数据库查询出错时不应抛异常"""
        bridge = ChatlogBridge(db_path=None)
        bridge._contact_cache = {}
        bridge._contact_cache_time = 0
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.Error("query failed")

        with patch.object(bridge, '_get_contact_db', return_value=mock_db), \
             patch.object(bridge, '_load_session_names'):
            bridge._load_contacts()

        # 缓存应为空（加载失败）
        self.assertEqual(bridge._contact_cache, {})


class TestGetMessagesAdditional(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def setUp(self, mock_ensure):
        self.bridge = ChatlogBridge(db_path=None)
        self.bridge._msg_db = None
        self.bridge._contact_db = None

    def test_bytes_content_message(self):
        """消息内容为 bytes 类型时应正确解码"""
        talker = "friend1"
        content = "字节消息内容".encode('utf-8')
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 1, 1, 1700000000, None, 0),
            ],
        )
        # 需要用 bytes 列，直接用 SQL 更新
        tn = _talker_to_table_name(talker)
        # 先删除再插入 bytes
        msg_db.execute(f"DELETE FROM [{tn}]")
        msg_db.execute(
            f"INSERT INTO [{tn}] VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, 100, 1, 1, 1700000000, content, 0),
        )
        msg_db.commit()

        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "字节消息内容")

    def test_unknown_msg_type_maps_to_other(self):
        """未知消息类型应映射为 'other'"""
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 999, 1, 1700000000, "未知类型", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].msg_type, 'other')

    def test_db_error_returns_empty(self):
        """数据库查询出错时返回空列表"""
        talker = "friend1"
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.Error("query error")

        with patch.object(self.bridge, '_get_msg_db', return_value=mock_db):
            result = self.bridge.get_messages(talker)

        self.assertEqual(result, [])

    def test_invalid_create_time(self):
        """无效的 create_time 不应导致崩溃"""
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 1, 1, None, "test", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].timestamp, '')

    def test_empty_sender_username(self):
        """sender_username 为空时应正常处理"""
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, None, 0)],
            rows=[
                (1, 1, 100, 1, 1, 1700000000, "test", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(len(result), 1)

    def test_type_49_unknown_sub_type_maps_to_link(self):
        """type=49 且 sub_type 不在 MSG_SUB_TYPE_MAP 中应映射为 link"""
        talker = "friend1"
        xml = '<appmsg type="9999"><title>未知</title></appmsg>'
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
            rows=[
                (1, 1, 100, 49, 1, 1700000000, xml, 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].msg_type, 'link')

    def test_chatroom_message_no_colon_split(self):
        """群聊消息中没有 :\n 分隔时，content 保持原样"""
        talker = "group@chatroom"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, "sender_wxid", 0)],
            rows=[
                (1, 1, 100, 1, 1, 1700000000, "没有冒号分隔的消息", 0),
            ],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(self.bridge, '_load_contacts'):
            self.bridge._contact_cache = {}
            result = self.bridge.get_messages(talker)

        self.assertEqual(result[0].content, "没有冒号分隔的消息")

    def test_validate_table_name_error_propagates(self):
        """非法 talker 导致表名验证失败时 ValueError 会传播"""
        # _talker_to_table_name 使用 md5，总是合法的
        # 但我们可以 mock _validate_table_name 抛异常
        talker = "friend1"
        msg_db = _make_msg_db(
            talker=talker,
            name2id_rows=[(1, talker, 0)],
        )
        with patch.object(self.bridge, '_get_msg_db', return_value=msg_db), \
             patch('chatlens.core.chatlog_bridge._validate_table_name', side_effect=ValueError("bad")):
            with self.assertRaises(ValueError):
                self.bridge.get_messages(talker)


class TestEnsureSchemaVersionAdditional(unittest.TestCase):
    def test_no_db_returns_early(self):
        with patch.object(ChatlogBridge, '_ensure_schema_version', lambda self: None):
            bridge = ChatlogBridge(db_path=None)
        with patch.object(bridge, '_get_msg_db', return_value=None):
            bridge._ensure_schema_version()
        # 不应抛异常

    def test_db_error_handled(self):
        db = MagicMock()
        db.execute.side_effect = sqlite3.Error("fail")
        with patch.object(ChatlogBridge, '_ensure_schema_version', lambda self: None):
            bridge = ChatlogBridge(db_path=None)
        with patch.object(bridge, '_get_msg_db', return_value=db):
            bridge._ensure_schema_version()
        # 不应抛异常

    def test_migration_from_zero(self):
        """从版本 0 升级到当前版本"""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        with patch.object(ChatlogBridge, '_ensure_schema_version', lambda self: None):
            bridge = ChatlogBridge(db_path=None)
        with patch.object(bridge, '_get_msg_db', return_value=db):
            bridge._ensure_schema_version()
        cursor = db.execute("SELECT value FROM _meta WHERE key='schema_version'")
        row = cursor.fetchone()
        self.assertEqual(int(row['value']), ChatlogBridge.CURRENT_SCHEMA_VERSION)

    def test_already_current_version(self):
        """已是当前版本时不再执行迁移"""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO _meta (key, value) VALUES ('schema_version', ?)",
                    (str(ChatlogBridge.CURRENT_SCHEMA_VERSION),))
        db.commit()

        with patch.object(ChatlogBridge, '_ensure_schema_version', lambda self: None):
            bridge = ChatlogBridge(db_path=None)
        with patch.object(bridge, '_get_msg_db', return_value=db), \
             patch.object(bridge, '_run_migration') as mock_migrate:
            bridge._ensure_schema_version()
            mock_migrate.assert_not_called()


class TestGetAllTalkersErrorHandling(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_db_error_returns_empty(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.Error("fail")
        with patch.object(bridge, '_get_msg_db', return_value=mock_db):
            result = bridge.get_all_talkers()
        self.assertEqual(result, [])

    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_count_error_returns_zero(self, mock_ensure):
        """单个 talker 计数查询失败时 count 为 0"""
        bridge = ChatlogBridge(db_path=None)
        talker = "user1"
        msg_db = _make_msg_db(
            name2id_rows=[(1, talker, 1)],
        )
        # 不创建对应的消息表，COUNT 查询会失败
        with patch.object(bridge, '_get_msg_db', return_value=msg_db), \
             patch.object(bridge, '_load_contacts'):
            bridge._contact_cache = {}
            result = bridge.get_all_talkers()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['message_count'], 0)


class TestRunMigration(unittest.TestCase):
    @patch.object(ChatlogBridge, '_ensure_schema_version')
    def test_migration_1_is_noop(self, mock_ensure):
        bridge = ChatlogBridge(db_path=None)
        # version 1 的迁移是 pass，不应抛异常
        bridge._run_migration(1)


if __name__ == '__main__':
    unittest.main()

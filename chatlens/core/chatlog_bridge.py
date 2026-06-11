import atexit
import re
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import urllib.request
import urllib.error

from .models import ChatMessage

import zstandard as zstd

# G4-2.1: 业务指标埋点
try:
    from chatlens._metrics import REGISTRY
except Exception:  # pragma: no cover
    REGISTRY = None  # type: ignore[assignment]

_zstd_d = zstd.ZstdDecompressor()

logger = logging.getLogger("chatlens.chatlog_bridge")


def _publish_db_metrics() -> None:
    """G4-2.1: 把 session LRU 缓存大小 + WAL 文件大小同步到 Prometheus Gauge。

    计数口径：活跃 SQLite 连接 = session LRU 缓存（msg / contact 各 1 + session ≤5）
    """
    if REGISTRY is None:
        return
    try:
        with _SESSION_DB_CACHE_LOCK:
            session_count = len(_SESSION_DB_CACHE)
        # msg + contact 视为常驻 2 条；session 缓存按实际 LRU 大小
        active = 2 + session_count
        REGISTRY.db_connections_active.set(active)
        # WAL 大小：仅统计 msg / contact 主库的 .db-wal
        wal_total = 0
        for db_path_attr in ("db_path",):
            db_path = globals().get(db_path_attr)
            if not db_path:
                continue
            wal = db_path + "-wal"
            if os.path.exists(wal):
                try:
                    wal_total += os.path.getsize(wal)
                except OSError:
                    pass
        REGISTRY.wal_size_bytes.set(wal_total)
    except Exception:  # pragma: no cover
        # 指标埋点绝不能影响主流程
        logger.debug("db_metrics 埋点失败", exc_info=True)

# session.db LRU 缓存（按 path 维度，最多 5 个条目；P2 T8 AC2.5）
_SESSION_DB_CACHE_SIZE = 5
_SESSION_DB_CACHE: "OrderedDict[str, Tuple[sqlite3.Connection, float]]" = OrderedDict()
_SESSION_DB_CACHE_LOCK = threading.Lock()

MSG_TYPE_MAP = {
    1: "text",
    3: "image",
    34: "voice",
    42: "personal_card",
    43: "video",
    47: "emotion",
    48: "location",
    49: "link",
    50: "other",
    10000: "system",
    10002: "system",
}

MSG_SUB_TYPE_MAP = {
    1: "text",
    4: "link",
    5: "link",
    6: "file",
    8: "emotion",
    19: "merge",
    24: "note",
    33: "link",
    36: "link",
    51: "link",
    57: "quote",
    62: "other",
    63: "link",
    87: "other",
    92: "link",
    2000: "other",
    2001: "other",
}

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _try_zstd_decompress(data: bytes) -> str:
    if data[:4] == ZSTD_MAGIC:
        try:
            return _zstd_d.decompress(data).decode("utf-8", errors="replace")  # type: ignore[no-any-return]
        except (OSError, ValueError) as e:
            logger.debug(f"zstd 解压失败: {e}")
            return ""
    return data.decode("utf-8", errors="replace")


def _configure_chatlog_sqlite(db: sqlite3.Connection) -> None:
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass


def _get_session_db(path: str) -> Optional[sqlite3.Connection]:
    """P2 T8 AC2.5: session.db LRU 缓存（maxsize=5）。

    多线程并发调用 `_load_session_names` 时复用同一 connection，避免 R5 的 FD 耗尽。
    缓存以 `path` 为键，命中即刷新 LRU 顺序；容量超限弹出最旧条目并 close。
    """
    with _SESSION_DB_CACHE_LOCK:
        entry = _SESSION_DB_CACHE.get(path)
        if entry is not None:
            db, _ts = entry
            # 探活：连接断了就重建
            try:
                db.execute("SELECT 1 LIMIT 1")
                _SESSION_DB_CACHE.move_to_end(path)
                return db
            except sqlite3.Error:
                _SESSION_DB_CACHE.pop(path, None)
                try:
                    db.close()
                except Exception:
                    pass
        if not os.path.exists(path):
            return None
        try:
            db = sqlite3.connect(path, check_same_thread=False)
            db.row_factory = sqlite3.Row
            _configure_chatlog_sqlite(db)
        except (sqlite3.Error, OSError) as e:
            logger.debug(f"打开 session.db 失败: {e}")
            return None
        _SESSION_DB_CACHE[path] = (db, time.time())
        # LRU 容量限制
        while len(_SESSION_DB_CACHE) > _SESSION_DB_CACHE_SIZE:
            time.sleep(0.05)  # 让出 CPU，防止紧循环
            oldest_path, (oldest_db, _ts) = _SESSION_DB_CACHE.popitem(last=False)
            try:
                oldest_db.close()
            except Exception:
                pass
            logger.debug(f"LRU 淘汰 session.db: {oldest_path}")
        return db


def _close_all_session_dbs() -> None:
    """atexit 钩子：清空 LRU 缓存，关闭所有 session.db 连接。"""
    with _SESSION_DB_CACHE_LOCK:
        for path, (db, _ts) in list(_SESSION_DB_CACHE.items()):
            try:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass
        _SESSION_DB_CACHE.clear()


# atexit 注册：正常 Python 退出时关闭 LRU 缓存中的所有 session.db
atexit.register(_close_all_session_dbs)


def _talker_to_table_name(talker: str) -> str:
    md5 = hashlib.md5(talker.encode("utf-8")).hexdigest()
    return f"Msg_{md5}"


_SAFE_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_table_name(table_name: str) -> None:
    """验证表名只包含安全字符（字母数字和下划线），防止 SQL 注入"""
    if not _SAFE_TABLE_RE.match(table_name):
        raise ValueError(f"非法表名: {table_name!r}")


class ChatlogBridge:
    CURRENT_SCHEMA_VERSION = 1
    _CONTACT_CACHE_TTL = 600  # 联系人缓存过期时间（秒）

    def __init__(self, api_base: str = "", db_path: Optional[str] = None) -> None:
        from chatlens._defaults import DEFAULT_CHATLOG_API_BASE

        if not api_base:
            api_base = DEFAULT_CHATLOG_API_BASE
        self.api_base = api_base.rstrip("/")
        self.db_path = db_path
        self._msg_db: Optional[sqlite3.Connection] = None
        self._contact_db: Optional[sqlite3.Connection] = None
        self._contact_cache: Dict[str, Dict] = {}
        self._chatroom_cache: Dict[str, Dict] = {}
        self._contact_cache_time: float = 0  # 联系人缓存加载时间戳
        self._db_lock = threading.Lock()
        self._ensure_schema_version()
        # P2 T8 AC2.4: 注册 atexit 钩子，保证正常 Python 退出时跑 WAL checkpoint + 关闭所有 db
        # 用 weakref 防止桥对象被 GC 释放后 atexit 仍尝试访问
        import weakref

        self._atexit_ref = weakref.ref(self)
        atexit.register(self._atexit_close_all)

    def _atexit_close_all(self) -> None:
        """atexit 钩子：checkpoint WAL + 关闭所有 db 连接（msg / contact / session LRU）。

        P2 T8 AC2.4: 正常 Python 退出时调用，保证 *.db-wal / *.db-shm 文件不残留。
        """
        try:
            self.checkpoint_wal()
        except Exception as e:
            logger.debug(f"atexit checkpoint_wal 失败: {e}")
        # 关闭 msg / contact 连接
        for attr in ("_msg_db", "_contact_db"):
            db = getattr(self, attr, None)
            if db is not None:
                try:
                    db.close()
                except Exception as e:
                    logger.debug(f"atexit 关闭 {attr} 失败: {e}")
                setattr(self, attr, None)
        # 关闭 LRU 缓存的 session.db
        try:
            _close_all_session_dbs()
        except Exception as e:
            logger.debug(f"atexit 关闭 session.db LRU 失败: {e}")

    def checkpoint_wal(self) -> None:
        """P2 T8 AC2.6: 对 _msg_db 和 _contact_db 跑 PRAGMA wal_checkpoint(TRUNCATE)。

        TRUNCATE 模式：把 WAL 内容合并回主 db 文件并把 WAL 文件截断到 0 字节。
        在 WebService.shutdown 的 reset_connections 之前调用，保证 kill 主进程
        时 *.db-wal / *.db-shm 不残留。
        """
        with self._db_lock:
            for attr in ("_msg_db", "_contact_db"):
                db = getattr(self, attr)
                if db is None:
                    continue
                try:
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error as e:
                    logger.warning(f"WAL checkpoint 失败 ({attr}): {e}")
                except Exception as e:
                    logger.debug(f"WAL checkpoint 异常 ({attr}): {e}")

    def _ensure_schema_version(self) -> None:
        db = self._get_msg_db()
        if not db:
            return
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            cursor = db.execute("SELECT value FROM _meta WHERE key='schema_version'")
            row = cursor.fetchone()
            current_version = int(row["value"]) if row else 0
            if current_version < self.CURRENT_SCHEMA_VERSION:
                for version in range(
                    current_version + 1, self.CURRENT_SCHEMA_VERSION + 1
                ):
                    self._run_migration(version)
                db.execute(
                    "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
                    (str(self.CURRENT_SCHEMA_VERSION),),
                )
                db.commit()
                logger.info(f"数据库 schema 已升级到版本 {self.CURRENT_SCHEMA_VERSION}")
        except (sqlite3.Error, OSError) as e:
            logger.error(f"schema 版本检查失败: {e}")

    def _run_migration(self, version: int) -> None:
        if version == 1:
            pass

    def _find_db_dir(self) -> Optional[str]:
        base = os.path.join(os.path.dirname(__file__), "..", "..", "chatlog_alpha")
        base = os.path.normpath(base)
        candidates = [
            os.path.join(base, "db_storage"),
            os.path.join(base, "work", "db_storage"),
        ]
        best = None
        best_mtime: float = 0
        for d in candidates:
            msg_file = os.path.join(d, "message", "message_0.db")
            if os.path.exists(msg_file):
                mtime = os.path.getmtime(msg_file)
                if mtime > best_mtime:
                    best_mtime = mtime
                    best = d
        if best:
            return best
        for d in candidates:
            if os.path.exists(d):
                return d
        return None

    def _get_msg_db(self) -> Optional[sqlite3.Connection]:
        # Bug5 修复：原实现完全没加锁，多线程并发调用时
        # 会出现"读 self._msg_db 时为 None → 各自建连接 → 互相覆盖赋值 → 句柄泄漏"
        # 把整段"检查 + 建连 + 赋值"放在 _db_lock 里串行化。
        # SELECT 1 探活本身是单连接线程安全操作，外层加锁后不会有性能问题。
        with self._db_lock:
            if self._msg_db:
                try:
                    self._msg_db.execute("SELECT 1 LIMIT 1")
                    return self._msg_db
                except sqlite3.Error:
                    logger.warning("消息数据库连接已断开，尝试重连...")
                    try:
                        self._msg_db.close()
                    except Exception:
                        logger.debug("关闭消息数据库连接时出错", exc_info=True)
                    self._msg_db = None
            msg_path = None
            if self.db_path and os.path.exists(self.db_path):
                msg_path = self.db_path
            else:
                db_dir = self._find_db_dir()
                if db_dir:
                    msg_path = os.path.join(db_dir, "message", "message_0.db")
            if not msg_path or not os.path.exists(msg_path):
                return None
            try:
                self._msg_db = sqlite3.connect(msg_path, check_same_thread=False)
                self._msg_db.row_factory = sqlite3.Row
                _configure_chatlog_sqlite(self._msg_db)
                logger.info(f"已连接 chatlog 消息数据库: {msg_path}")
                _publish_db_metrics()
                return self._msg_db
            except (sqlite3.Error, OSError) as e:
                logger.error(f"连接消息数据库失败: {e}")
                return None

    def _get_contact_db(self) -> Optional[sqlite3.Connection]:
        # P2 T8 AC2.1: 与 _get_msg_db 同模式 — `with self._db_lock:` 包住
        # 探活 + 建连，避免多线程并发建连互相覆盖导致句柄泄漏。
        with self._db_lock:
            if self._contact_db:
                try:
                    self._contact_db.execute("SELECT 1 LIMIT 1")
                    return self._contact_db
                except sqlite3.Error:
                    logger.warning("联系人数据库连接已断开，尝试重连...")
                    try:
                        self._contact_db.close()
                    except Exception:
                        logger.debug("关闭联系人数据库连接时出错", exc_info=True)
                    self._contact_db = None
            db_dir = self._find_db_dir()
            if not db_dir:
                return None
            contact_path = os.path.join(db_dir, "contact", "contact.db")
            if not os.path.exists(contact_path):
                return None
            try:
                self._contact_db = sqlite3.connect(contact_path, check_same_thread=False)
                self._contact_db.row_factory = sqlite3.Row
                _configure_chatlog_sqlite(self._contact_db)
                logger.info(f"已连接 chatlog 联系人数据库: {contact_path}")
                _publish_db_metrics()
                return self._contact_db
            except (sqlite3.Error, OSError) as e:
                logger.error(f"连接联系人数据库失败: {e}")
                return None

    def _load_contacts(self) -> None:
        if self._contact_cache and (
            time.time() - self._contact_cache_time < self._CONTACT_CACHE_TTL
        ):
            return
        self._contact_cache = {}
        db = self._get_contact_db()
        if not db:
            return
        try:
            cursor = db.execute(
                "SELECT username, nick_name, remark, alias FROM contact"
            )
            for row in cursor:
                self._contact_cache[row["username"]] = {
                    "nick_name": row["nick_name"] or "",
                    "remark": row["remark"] or "",
                    "alias": row["alias"] or "",
                }
            self._contact_cache_time = time.time()
            logger.info(f"已加载 {len(self._contact_cache)} 个联系人")
        except (sqlite3.Error, OSError) as e:
            logger.error(f"加载联系人失败: {e}")
        self._load_session_names()

    def _load_session_names(self) -> None:
        db_dir = self._find_db_dir()
        if not db_dir:
            return
        session_path = os.path.join(db_dir, "session", "session.db")
        if not os.path.exists(session_path):
            return
        # Bug3 修复：多线程并发时，_get_session_db 里的 "SELECT 1 探活" 可能
        # 误判 connection 损坏而 close 掉，导致正在迭代游标的线程抛
        # IndexError('tuple index out of range') 等异常。这里：
        # 1) 用 _db_lock 串行化整个流程，避免多线程同时迭代/关闭同一 connection
        # 2) 先 list(sdb.execute(...)) 把全部行读到内存，再处理；即使 connection
        #    之后被关，list 已经是安全副本
        with self._db_lock:
            try:
                sdb = _get_session_db(session_path)
                if sdb is None:
                    return
                try:
                    rows = list(sdb.execute(
                        "SELECT username, session_title FROM SessionNoContactInfoTable "
                        "WHERE session_title IS NOT NULL AND session_title != ''"
                    ))
                except (sqlite3.Error, OSError):
                    return
                for row in rows:
                    try:
                        username = row["username"]
                        title = row["session_title"] or ""
                    except (IndexError, KeyError, TypeError):
                        # 防御：行结构异常时跳过该行（不中断整次加载）
                        continue
                    if username in self._contact_cache:
                        info = self._contact_cache[username]
                        if not info.get("nick_name") and title:
                            info["nick_name"] = title
                    elif title:
                        self._contact_cache[username] = {
                            "nick_name": title,
                            "remark": "",
                            "alias": "",
                        }
                logger.info("已从 session.db 补充群名信息")
            except (sqlite3.Error, OSError) as e:
                logger.debug(f"从 session.db 加载群名失败: {e}")

    def reset_connections(self) -> None:
        """线程安全地重置数据库连接"""
        # P2 T8 AC2.6: 在重置之前先跑 WAL checkpoint（TRUNCATE 模式），
        # 避免 *.db-wal / *.db-shm 文件在 reset 后丢失的写入。
        try:
            self.checkpoint_wal()
        except Exception as e:
            logger.debug(f"reset_connections 前 WAL checkpoint 失败: {e}")
        with self._db_lock:
            if self._msg_db:
                try:
                    self._msg_db.close()
                except Exception:
                    logger.debug("重置时关闭消息数据库出错", exc_info=True)
            self._msg_db = None
            self._contact_db = None
            self._contact_cache = {}
            self._contact_cache_time = 0

    def _get_display_name(self, username: str) -> str:
        self._load_contacts()
        info = self._contact_cache.get(username, {})
        return info.get("remark") or info.get("nick_name") or username

    @staticmethod
    def _extract_sub_type(raw_content) -> int:
        """从 type=49 消息的 XML 内容中提取 appmsg type（即 sub_type）"""
        if not raw_content:
            return 0
        text = raw_content
        if isinstance(raw_content, bytes):
            text = _try_zstd_decompress(raw_content)
        try:
            import re

            match = re.search(r'<appmsg[^>]*\btype="(\d+)"', text)
            if match:
                return int(match.group(1))
        except (ValueError, TypeError):
            pass
        return 0

    def is_available(self) -> bool:
        return self._get_msg_db() is not None

    def get_chatrooms(self) -> List[Dict[str, Any]]:
        self._load_contacts()
        rooms = []
        for username, info in self._contact_cache.items():
            if username.endswith("@chatroom"):
                rooms.append(
                    {
                        "name": username,
                        "nickName": info.get("remark")
                        or info.get("nick_name")
                        or username,
                        "remark": info.get("remark", ""),
                    }
                )
        return rooms

    def get_all_talkers(self) -> List[Dict[str, Any]]:
        db = self._get_msg_db()
        if not db:
            return []
        try:
            # 一次拿全所有 Msg_<md5> 表名
            msg_tables = [
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                )
            ]
            # 一次 UNION ALL 拿全所有 Msg_<md5> 的行数（消除 N+1）
            count_map: Dict[str, int] = {}
            if msg_tables:
                for t in msg_tables:
                    _validate_table_name(t)
                union_sql = " UNION ALL ".join(
                    f"SELECT '{t}' AS t, COUNT(*) AS cnt FROM {t}" for t in msg_tables
                )
                for row in db.execute(union_sql):
                    count_map[row["t"]] = int(row["cnt"])

            cursor = db.execute("SELECT user_name, is_session FROM Name2Id")
            talkers = []
            for row in cursor:
                username = row["user_name"]
                if not username:
                    continue
                table_name = _talker_to_table_name(username)
                display_name = self._get_display_name(username)
                talkers.append(
                    {
                        "talker": username,
                        "display_name": display_name,
                        "message_count": count_map.get(table_name, 0),
                        "is_chatroom": username.endswith("@chatroom"),
                        "is_session": bool(row["is_session"]),
                    }
                )
            talkers.sort(key=lambda x: x["message_count"], reverse=True)
            return talkers
        except (sqlite3.Error, OSError) as e:
            logger.error(f"获取 talker 列表失败: {e}")
            return []

    def get_messages(
        self, talker: str, limit: int = 0, start_date: str = "", end_date: str = ""
    ) -> List[ChatMessage]:
        db = self._get_msg_db()
        if not db:
            return []

        table_name = _talker_to_table_name(talker)
        try:
            _validate_table_name(table_name)
            check = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            if not check.fetchone():
                logger.warning(f"表 {table_name} 不存在 (talker: {talker})")
                return []
        except (sqlite3.Error, OSError):
            return []

        try:
            query = f"""
                SELECT m.local_id, m.sort_seq, m.server_id, m.local_type,
                       n.user_name, m.create_time, m.message_content, m.status
                FROM {table_name} m
                LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid
                ORDER BY m.sort_seq ASC
            """
            if start_date or end_date:
                conditions = []
                if start_date:
                    try:
                        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
                        conditions.append(f"m.create_time >= {start_ts}")
                    except ValueError:
                        pass
                if end_date:
                    try:
                        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp()) + 86399
                        conditions.append(f"m.create_time <= {end_ts}")
                    except ValueError:
                        pass
                if conditions:
                    query = query.replace(
                        "ORDER BY", "WHERE " + " AND ".join(conditions) + " ORDER BY"
                    )
            if limit > 0:
                query += f" LIMIT {limit}"

            cursor = db.execute(query)
            messages = []
            is_chatroom = talker.endswith("@chatroom")

            for row in cursor:
                msg_type_code = row["local_type"]
                if msg_type_code == 49:
                    sub_type = self._extract_sub_type(row["message_content"])
                    msg_type = MSG_SUB_TYPE_MAP.get(sub_type, "link")
                else:
                    msg_type = MSG_TYPE_MAP.get(msg_type_code, "other")

                raw_content = row["message_content"]
                content = ""
                if isinstance(raw_content, bytes) and raw_content:
                    content = _try_zstd_decompress(raw_content)
                elif isinstance(raw_content, str):
                    content = raw_content

                sender_username = row["user_name"] or ""
                if is_chatroom and content:
                    parts = content.split(":\n", 1)
                    if len(parts) == 2:
                        sender_username = parts[0]
                        content = parts[1]

                sender_display = (
                    self._get_display_name(sender_username) if sender_username else ""
                )

                is_self = row["status"] == 2

                timestamp = ""
                create_time = row["create_time"]
                if create_time:
                    try:
                        timestamp = datetime.fromtimestamp(int(create_time)).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    except (ValueError, TypeError, OSError):
                        pass

                if msg_type_code == 49 and msg_type != "quote" and content:
                    if "<appmsg" in content or "<msg>" in content:
                        msg_type = "link"

                quote_content = ""
                if msg_type == "quote" and content:
                    quote_content = content[:200]

                if msg_type_code in (10000, 10002):
                    msg_type = "system"
                    msg_attr = "system"
                else:
                    msg_attr = "self" if is_self else "friend"

                messages.append(
                    ChatMessage(
                        sender=sender_display or sender_username,
                        content=content
                        if msg_type in ("text", "quote", "system", "other")
                        else "",
                        msg_type=msg_type,
                        msg_attr=msg_attr,
                        timestamp=timestamp,
                        group_name=talker,
                        sender_remark="",
                        quote_content=quote_content,
                    )
                )

            logger.info(f"从 chatlog 数据库读取 {len(messages)} 条消息: {talker}")
            return messages

        except (sqlite3.Error, OSError) as e:
            logger.error(f"从 chatlog 读取消息失败: {e}")
            return []

    def _api_get(self, path: str) -> Optional[Any]:
        url = self.api_base + path
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, TimeoutError):
            return None

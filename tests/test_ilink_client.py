"""ILinkClient 单元测试 — mock httpx.AsyncClient"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatlens.plugins.ilink.client import ILinkClient, ILINK_BASE, CHANNEL_VERSION


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Python 3.12 兼容性：确保主线程有 event loop

    不调用 asyncio.get_event_loop()，因为它在 Python 3.12 中已经
    被 DeprecationWarning 标记并将在未来变为 RuntimeError。
    """
    asyncio.set_event_loop(asyncio.new_event_loop())
    yield


def _run(coro):
    """同步运行异步协程的辅助函数"""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestILinkClientInit(unittest.TestCase):
    """测试 ILinkClient.__init__() 及配置加载"""

    def test_init_with_token(self):
        """传入 token 时直接连接"""
        client = ILinkClient(token="my-token")
        self.assertEqual(client.token, "my-token")
        self.assertTrue(client._connected)
        self.assertTrue(client.is_connected())

    def test_init_without_token(self):
        """无 token 时未连接"""
        with patch.object(ILinkClient, "_load_token"):
            client = ILinkClient(token="")
            self.assertEqual(client.token, "")
            self.assertFalse(client._connected)
            self.assertFalse(client.is_connected())

    def test_init_load_token_from_config(self):
        """无 token 时自动调用 _load_token"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "bot_token": "cfg-token",
                    "ilink_bot_id": "bot1",
                    "ilink_user_id": "user1@im.wechat",
                    "context_tokens": {"user1@im.wechat": "ctx_abc"},
                },
                f,
            )
            tmp = f.name

        try:
            client = ILinkClient(config_path=tmp)
            self.assertEqual(client.token, "cfg-token")
            self.assertEqual(client.bot_id, "bot1")
            self.assertEqual(client.user_id, "user1@im.wechat")
            self.assertEqual(client.context_tokens, {"user1@im.wechat": "ctx_abc"})
            self.assertTrue(client.is_connected())
        finally:
            os.unlink(tmp)

    def test_init_config_path_default(self):
        """默认 config_path 指向 config/ilink_token.json"""
        client = ILinkClient(token="t")
        expected = os.path.join(
            os.path.dirname(__file__), "..", "chatlens", "config", "ilink_token.json"
        )
        self.assertEqual(os.path.normpath(client._config_path), os.path.normpath(expected))

    def test_init_custom_config_path(self):
        """自定义 config_path"""
        client = ILinkClient(token="t", config_path="/tmp/custom.json")
        self.assertEqual(client._config_path, "/tmp/custom.json")

    def test_load_token_file_not_found(self):
        """配置文件不存在时不报错"""
        client = ILinkClient(token="t", config_path="/nonexistent/path.json")
        client.token = ""
        client._load_token()
        self.assertEqual(client.token, "")

    def test_load_token_invalid_json(self):
        """配置文件 JSON 格式错误时不报错"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            tmp = f.name

        try:
            client = ILinkClient(token="", config_path=tmp)
            self.assertEqual(client.token, "")
            self.assertFalse(client.is_connected())
        finally:
            os.unlink(tmp)

    def test_base_url(self):
        """base URL 为微信 iLink 地址"""
        client = ILinkClient(token="t")
        self.assertEqual(client.base, ILINK_BASE)

    def test_initial_state(self):
        """初始状态字段"""
        client = ILinkClient(token="t")
        self.assertEqual(client.bot_id, "")
        self.assertEqual(client.user_id, "")
        self.assertEqual(client.context_tokens, {})
        self.assertEqual(client._cursor, "")
        self.assertFalse(client._polling)
        self.assertIsNone(client._poll_task)
        self.assertIsNone(client._on_message)


class TestILinkClientHeaders(unittest.TestCase):
    """测试 _headers() 方法"""

    def test_headers_contain_required_fields(self):
        """请求头包含必要字段"""
        client = ILinkClient(token="test-token")
        headers = client._headers()
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["AuthorizationType"], "ilink_bot_token")
        self.assertIn("Bearer test-token", headers["Authorization"])
        self.assertIn("X-WECHAT-UIN", headers)

    def test_headers_uin_is_base64(self):
        """X-WECHAT-UIN 是 base64 编码"""
        import base64

        client = ILinkClient(token="t")
        headers = client._headers()
        uin = headers["X-WECHAT-UIN"]
        decoded = base64.b64decode(uin).decode()
        self.assertTrue(decoded.isdigit())


class TestILinkClientPost(unittest.TestCase):
    """测试 _post() 方法 — mock HTTP 请求"""

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_post_success(self, MockAsyncClient):
        """成功 POST 请求返回解析后的 JSON"""
        mock_resp = MagicMock()
        mock_resp.text = '{"ret": 0, "data": "ok"}'
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client._post("test_endpoint", {"key": "val"}))
        self.assertEqual(result["ret"], 0)
        self.assertEqual(result["data"], "ok")

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_post_empty_response(self, MockAsyncClient):
        """空响应返回默认 ret=0"""
        mock_resp = MagicMock()
        mock_resp.text = "{}"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client._post("test_endpoint", {}))
        self.assertEqual(result["ret"], 0)

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_post_http_error(self, MockAsyncClient):
        """HTTP 错误返回 ret=-1"""
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("connection failed"))
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client._post("test_endpoint", {}))
        self.assertEqual(result["ret"], -1)
        self.assertIn("connection failed", result["error"])

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_post_timeout(self, MockAsyncClient):
        """超时返回 ret=-1"""
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client._post("test_endpoint", {}))
        self.assertEqual(result["ret"], -1)
        self.assertIn("timeout", result["error"])

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_post_json_decode_error(self, MockAsyncClient):
        """JSON 解析错误返回 ret=-1"""
        mock_resp = MagicMock()
        mock_resp.text = "not json"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client._post("test_endpoint", {}))
        self.assertEqual(result["ret"], -1)

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_post_adds_channel_version(self, MockAsyncClient):
        """POST body 中自动添加 base_info.channel_version"""
        captured_body = {}

        mock_resp = MagicMock()
        mock_resp.text = '{"ret": 0}'
        mock_client = AsyncMock()
        mock_client.is_closed = False

        async def capture_post(*args, **kwargs):
            captured_body["content"] = kwargs.get("content", b"")
            return mock_resp

        mock_client.post = capture_post
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        _run(client._post("test_endpoint", {"key": "val"}))

        body = json.loads(captured_body["content"])
        self.assertEqual(body["base_info"]["channel_version"], CHANNEL_VERSION)

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_post_url_format(self, MockAsyncClient):
        """POST URL 格式正确"""
        captured_url = {}

        mock_resp = MagicMock()
        mock_resp.text = '{"ret": 0}'
        mock_client = AsyncMock()
        mock_client.is_closed = False

        async def capture_post(*args, **kwargs):
            captured_url["url"] = args[0] if args else kwargs.get("url", "")
            return mock_resp

        mock_client.post = capture_post
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        _run(client._post("getupdates", {}))

        self.assertEqual(captured_url["url"], f"{ILINK_BASE}/ilink/bot/getupdates")


class TestILinkClientGetUpdates(unittest.TestCase):
    """测试 get_updates() — 上下文获取"""

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_get_updates_returns_messages(self, MockAsyncClient):
        """获取消息列表"""
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(
            {
                "ret": 0,
                "get_updates_buf": "cursor_123",
                "msgs": [
                    {
                        "from_user_id": "user1@im.wechat",
                        "context_token": "ctx_token_1",
                        "content": "hello",
                    }
                ],
            }
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        msgs = _run(client.get_updates())
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["from_user_id"], "user1@im.wechat")

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_get_updates_updates_cursor(self, MockAsyncClient):
        """轮询后更新 cursor"""
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(
            {"ret": 0, "get_updates_buf": "new_cursor", "msgs": []}
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        _run(client.get_updates())
        self.assertEqual(client._cursor, "new_cursor")

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_get_updates_saves_context_tokens(self, MockAsyncClient):
        """消息中的 context_token 被保存"""
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(
            {
                "ret": 0,
                "get_updates_buf": "c1",
                "msgs": [
                    {"from_user_id": "u1", "context_token": "ct1"},
                    {"from_user_id": "u2", "context_token": "ct2"},
                ],
            }
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        with patch.object(ILinkClient, "_save_token"):
            client = ILinkClient(token="t")
            client._async_client = mock_client
            _run(client.get_updates())
            self.assertEqual(client.context_tokens["u1"], "ct1")
            self.assertEqual(client.context_tokens["u2"], "ct2")


class TestILinkClientSendMessage(unittest.TestCase):
    """测试 send_text() — 消息发送"""

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_send_text_success(self, MockAsyncClient):
        """发送文本消息成功"""
        mock_resp = MagicMock()
        mock_resp.text = '{"ret": 0}'
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        client.context_tokens["user1"] = "ctx_abc"
        result = _run(client.send_text("hello", "user1"))
        self.assertTrue(result)

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_send_text_with_explicit_context_token(self, MockAsyncClient):
        """显式传入 context_token"""
        mock_resp = MagicMock()
        mock_resp.text = '{"ret": 0}'
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client.send_text("hello", "user1", context_token="explicit_ctx"))
        self.assertTrue(result)

    def test_send_text_no_context_token_fails(self):
        """无 context_token 时发送失败"""
        client = ILinkClient(token="t")
        client.context_tokens = {}
        result = _run(client.send_text("hello", "user1"))
        self.assertFalse(result)

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_send_text_api_failure(self, MockAsyncClient):
        """API 返回非零 ret 时发送失败"""
        mock_resp = MagicMock()
        mock_resp.text = '{"ret": -1, "error": "forbidden"}'
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        client.context_tokens["user1"] = "ctx_abc"
        result = _run(client.send_text("hello", "user1"))
        self.assertFalse(result)


class TestILinkClientSendTyping(unittest.TestCase):
    """测试 send_typing()"""

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_send_typing_with_context(self, MockAsyncClient):
        """有 context_token 时发送 typing"""
        mock_resp = MagicMock()
        mock_resp.text = '{"ret": 0}'
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        client.context_tokens["user1"] = "ctx_abc"
        _run(client.send_typing("user1"))
        mock_client.post.assert_called_once()

    def test_send_typing_no_context(self):
        """无 context_token 时跳过"""
        client = ILinkClient(token="t")
        client.context_tokens = {}
        _run(client.send_typing("user1"))
        # 不应抛出异常


class TestILinkClientGetContacts(unittest.TestCase):
    """测试联系人获取相关 — get_updates 中的 context_tokens"""

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_get_updates_accumulates_contacts(self, MockAsyncClient):
        """多次 get_updates 积累联系人 context_token"""
        mock_client = AsyncMock()
        mock_client.is_closed = False

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.text = json.dumps(
                    {
                        "ret": 0,
                        "get_updates_buf": "c1",
                        "msgs": [{"from_user_id": "u1", "context_token": "ct1"}],
                    }
                )
            else:
                resp.text = json.dumps(
                    {
                        "ret": 0,
                        "get_updates_buf": "c2",
                        "msgs": [{"from_user_id": "u2", "context_token": "ct2"}],
                    }
                )
            return resp

        mock_client.post = mock_post
        MockAsyncClient.return_value = mock_client

        with patch.object(ILinkClient, "_save_token"):
            client = ILinkClient(token="t")
            client._async_client = mock_client
            _run(client.get_updates())
            _run(client.get_updates())
            self.assertEqual(len(client.context_tokens), 2)
            self.assertEqual(client.context_tokens["u1"], "ct1")
            self.assertEqual(client.context_tokens["u2"], "ct2")


class TestILinkClientLoginQRCode(unittest.TestCase):
    """测试 login_qrcode() — 认证流程"""

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_login_qrcode_success(self, MockAsyncClient):
        """成功获取二维码 URL"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"qrcode_img_content": "https://qr.example.com/123"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client.login_qrcode())
        self.assertEqual(result, "https://qr.example.com/123")

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_login_qrcode_fallback_key(self, MockAsyncClient):
        """qrcode 字段回退"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"qrcode": "https://qr.example.com/fallback"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client.login_qrcode())
        self.assertEqual(result, "https://qr.example.com/fallback")

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_login_qrcode_http_error(self, MockAsyncClient):
        """HTTP 错误返回 None"""
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("fail"))
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client.login_qrcode())
        self.assertIsNone(result)


class TestILinkClientWaitForScan(unittest.TestCase):
    """测试 wait_for_scan() — 扫码认证"""

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_wait_for_scan_confirmed(self, MockAsyncClient):
        """扫码确认成功"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "confirmed",
            "bot_token": "new-token",
            "ilink_bot_id": "bot1",
            "ilink_user_id": "user1",
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        with patch.object(ILinkClient, "_save_token"):
            client = ILinkClient(token="t")
            client._async_client = mock_client
            result = _run(client.wait_for_scan("qr_key", timeout=1))
            self.assertTrue(result)
            self.assertEqual(client.token, "new-token")
            self.assertTrue(client._connected)

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_wait_for_scan_expired(self, MockAsyncClient):
        """二维码过期返回 False"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "expired"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        MockAsyncClient.return_value = mock_client

        client = ILinkClient(token="t")
        client._async_client = mock_client
        result = _run(client.wait_for_scan("qr_key", timeout=1))
        self.assertFalse(result)


class TestILinkClientClose(unittest.TestCase):
    """测试 close() — 关闭连接"""

    def test_close_with_client(self):
        """关闭已有的异步客户端"""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()

        client = ILinkClient(token="t")
        client._async_client = mock_client
        _run(client.close())
        mock_client.aclose.assert_called_once()
        self.assertIsNone(client._async_client)

    def test_close_without_client(self):
        """无客户端时不报错"""
        client = ILinkClient(token="t")
        client._async_client = None
        _run(client.close())  # 不应抛异常

    def test_close_already_closed(self):
        """客户端已关闭时不重复关闭"""
        mock_client = MagicMock()
        mock_client.is_closed = True

        client = ILinkClient(token="t")
        client._async_client = mock_client
        _run(client.close())
        mock_client.aclose.assert_not_called()


class TestILinkClientGetClient(unittest.TestCase):
    """测试 _get_client() 懒初始化"""

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_get_client_creates_new(self, MockAsyncClient):
        """首次调用创建新客户端"""
        mock_instance = MagicMock()
        mock_instance.is_closed = False
        MockAsyncClient.return_value = mock_instance

        client = ILinkClient(token="t")
        result = client._get_client()
        MockAsyncClient.assert_called_once()
        self.assertEqual(result, mock_instance)

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_get_client_reuses_existing(self, MockAsyncClient):
        """已有客户端时复用"""
        mock_instance = MagicMock()
        mock_instance.is_closed = False

        client = ILinkClient(token="t")
        client._async_client = mock_instance
        result = client._get_client()
        MockAsyncClient.assert_not_called()
        self.assertEqual(result, mock_instance)

    @patch("chatlens.plugins.ilink.client.httpx.AsyncClient")
    def test_get_client_recreates_if_closed(self, MockAsyncClient):
        """客户端已关闭时重新创建"""
        closed_client = MagicMock()
        closed_client.is_closed = True
        new_client = MagicMock()
        new_client.is_closed = False
        MockAsyncClient.return_value = new_client

        client = ILinkClient(token="t")
        client._async_client = closed_client
        result = client._get_client()
        MockAsyncClient.assert_called_once()
        self.assertEqual(result, new_client)


class TestILinkClientStartStop(unittest.TestCase):
    """测试 start/stop 消息监听"""

    def test_start_sets_polling(self):
        """start() 设置轮询标志"""
        client = ILinkClient(token="t")
        callback = MagicMock()
        _run(client.start(callback))
        self.assertTrue(client._polling)
        self.assertEqual(client._on_message, callback)
        # 清理
        _run(client.stop())

    def test_start_idempotent(self):
        """重复 start 不创建多个轮询任务"""
        client = ILinkClient(token="t")
        callback = MagicMock()
        _run(client.start(callback))
        first_task = client._poll_task
        _run(client.start(callback))
        self.assertEqual(client._poll_task, first_task)
        # 清理
        _run(client.stop())

    def test_stop_clears_polling(self):
        """stop() 清除轮询标志"""
        client = ILinkClient(token="t")
        callback = MagicMock()
        _run(client.start(callback))
        _run(client.stop())
        self.assertFalse(client._polling)
        self.assertIsNone(client._poll_task)


class TestILinkClientSaveToken(unittest.TestCase):
    """测试 _save_token()"""

    def test_save_token_creates_file(self):
        """保存 token 到文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sub", "token.json")
            client = ILinkClient(token="t", config_path=path)
            client.token = "saved-tok"
            client.bot_id = "bid"
            client.user_id = "uid"
            client.context_tokens = {"u1": "c1"}
            client._save_token()

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["bot_token"], "saved-tok")
            self.assertEqual(data["ilink_bot_id"], "bid")
            self.assertEqual(data["ilink_user_id"], "uid")
            self.assertEqual(data["context_tokens"], {"u1": "c1"})


class TestILinkClientGetBindUserId(unittest.TestCase):
    """测试 get_bind_user_id()"""

    def test_returns_user_id(self):
        client = ILinkClient(token="t")
        client.user_id = "user123@im.wechat"
        self.assertEqual(client.get_bind_user_id(), "user123@im.wechat")

    def test_returns_empty_when_not_set(self):
        client = ILinkClient(token="t")
        self.assertEqual(client.get_bind_user_id(), "")


if __name__ == "__main__":
    unittest.main()

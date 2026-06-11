"""微信 iLink Bot 客户端 — 基于微信官方 ClawBot/iLink 协议"""

import asyncio
import base64
import json
import logging
import os
import random
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger("chatlens.plugins.ilink")

ILINK_BASE = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.3"

# H3 修复：token 写入 debounce（秒）。同一秒内多次 getupdates 返回消息只写一次 token。
_TOKEN_SAVE_DEBOUNCE = 1.0


class ILinkClient:
    """iLink Bot API 客户端"""

    def __init__(self, token: str = "", config_path: str = ""):
        self.base = ILINK_BASE
        self.token = token
        self.bot_id = ""
        self.user_id = ""
        self.context_tokens: Dict[str, str] = {}
        self._cursor = ""
        self._connected = bool(token)
        self._polling = False
        self._poll_task: Optional[asyncio.Task] = None
        self._on_message: Optional[Callable] = None
        self._lock = asyncio.Lock()
        self._async_client: Optional[httpx.AsyncClient] = None
        self._config_path = config_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "ilink_token.json"
        )
        # H3 修复：debounce 状态：上次落盘时间戳 + 锁
        self._last_save_ts: float = 0.0
        self._save_lock = threading.Lock()
        if not self.token:
            self._load_token()

    def _load_token(self) -> None:
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.token = data.get("bot_token", "")
                self.bot_id = data.get("ilink_bot_id", "")
                self.user_id = data.get("ilink_user_id", "")
                self.context_tokens = data.get("context_tokens", {})
                if self.token:
                    self._connected = True
                    logger.info("已从配置加载 iLink token")
            except (OSError, ValueError) as e:
                logger.warning(f"加载 iLink token 失败: {e}")

    def _save_token(self) -> None:
        """保存 token 到文件。

        H3 修复：
        1. 加 1s debounce：同一秒内多次调用只写一次；
        2. 由调用方负责用 `asyncio.to_thread(self._save_token)` 包装以避免阻塞事件循环。
        """
        with self._save_lock:
            now = time.time()
            if now - self._last_save_ts < _TOKEN_SAVE_DEBOUNCE:
                return
            self._last_save_ts = now
        data = {
            "bot_token": self.token,
            "ilink_bot_id": self.bot_id,
            "ilink_user_id": self.user_id,
            "context_tokens": self.context_tokens,
        }
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"保存 iLink token 失败: {e}")

    def _headers(self) -> Dict[str, str]:
        uin = base64.b64encode(str(random.randint(0, 0xFFFFFFFF)).encode()).decode()
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.token}",
            "X-WECHAT-UIN": uin,
        }

    def _get_client(self) -> httpx.AsyncClient:
        """懒初始化异步 HTTP 客户端"""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient()
        return self._async_client

    async def close(self) -> None:
        """关闭异步客户端"""
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()
            self._async_client = None

    async def _post(self, endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
        body["base_info"] = {"channel_version": CHANNEL_VERSION}
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = self._headers()
        headers["Content-Length"] = str(len(raw))
        client = self._get_client()
        # getupdates 用 30s 长轮询，其它接口保持 10s 短超时
        timeout = 30 if endpoint == "getupdates" else 10
        try:
            resp = await client.post(
                f"{self.base}/ilink/bot/{endpoint}",
                content=raw,
                headers=headers,
                timeout=timeout,
            )
            text = resp.text.strip()
            if text and text != "{}":
                return json.loads(text)  # type: ignore[no-any-return]
            return {"ret": 0}
        except (
            httpx.HTTPError,
            httpx.TimeoutException,
            json.JSONDecodeError,
            OSError,
        ) as e:
            logger.error(f"iLink API 请求失败 [{endpoint}]: {e}")
            return {"ret": -1, "error": str(e)}

    def is_connected(self) -> bool:
        return self._connected and bool(self.token)

    async def login_qrcode(self) -> Optional[str]:
        """获取登录二维码 URL，返回二维码链接或 None"""
        client = self._get_client()
        try:
            resp = await client.get(
                f"{self.base}/ilink/bot/get_bot_qrcode?bot_type=3",
                timeout=10,
            )
            data = resp.json()
            return data.get("qrcode_img_content") or data.get("qrcode")  # type: ignore[no-any-return]
        except (
            httpx.HTTPError,
            httpx.TimeoutException,
            json.JSONDecodeError,
            OSError,
        ) as e:
            logger.error(f"获取 iLink 二维码失败: {e}")
            return None

    async def wait_for_scan(self, qrcode_key: str, timeout: int = 120) -> bool:
        """等待用户扫码确认，返回是否成功"""
        client = self._get_client()
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = await client.get(
                    f"{self.base}/ilink/bot/get_qrcode_status?qrcode={qrcode_key}",
                    headers={"iLink-App-ClientVersion": "1"},
                    timeout=40,
                )
                status = resp.json()
                st = status.get("status", "")
                if st == "confirmed":
                    self.token = status.get("bot_token", "")
                    self.bot_id = status.get("ilink_bot_id", "")
                    self.user_id = status.get("ilink_user_id", "")
                    self._connected = True
                    # H3 修复：用 asyncio.to_thread 包装避免阻塞事件循环
                    await asyncio.to_thread(self._save_token)
                    logger.info("iLink 扫码登录成功")
                    return True
                elif st == "expired":
                    logger.warning("iLink 二维码已过期")
                    return False
                elif st == "scaned":
                    logger.info("已扫码，等待确认...")
            except (
                httpx.HTTPError,
                httpx.TimeoutException,
                json.JSONDecodeError,
                OSError,
            ):
                pass
            await asyncio.sleep(3)
        return False

    async def get_updates(self) -> List[Dict[str, Any]]:
        """长轮询获取新消息，自动更新 context_token"""
        result = await self._post(
            "getupdates",
            {
                "get_updates_buf": self._cursor,
            },
        )
        async with self._lock:
            self._cursor = result.get("get_updates_buf", self._cursor)
            msgs = result.get("msgs", [])
            for msg in msgs:
                from_id = msg.get("from_user_id", "")
                ct = msg.get("context_token", "")
                if ct and from_id:
                    self.context_tokens[from_id] = ct
            if msgs:
                # H3 修复：用 asyncio.to_thread 包装避免阻塞事件循环
                # _save_token 内部已有 1s debounce，多次调用会合并
                await asyncio.to_thread(self._save_token)
        return msgs  # type: ignore[no-any-return]

    async def send_text(
        self, text: str, to_user_id: str, context_token: str = ""
    ) -> bool:
        """发送文本消息"""
        async with self._lock:
            if not context_token:
                context_token = self.context_tokens.get(to_user_id, "")
        if not context_token:
            logger.warning(f"无 context_token，无法发送消息给 {to_user_id}")
            return False
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"chatlens-{uuid.uuid4().hex[:12]}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
        }
        result = await self._post("sendmessage", body)
        ret = result.get("ret", 0)
        return ret == 0  # type: ignore[no-any-return]

    async def send_typing(self, to_user_id: str) -> None:
        """发送"正在输入"状态"""
        async with self._lock:
            context_token = self.context_tokens.get(to_user_id, "")
        if not context_token:
            return
        await self._post(
            "sendtyping",
            {
                "to_user_id": to_user_id,
                "context_token": context_token,
            },
        )

    async def start(self, on_message: Callable) -> None:
        """启动长轮询消息监听"""
        if self._polling:
            return
        self._on_message = on_message
        self._polling = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("iLink 消息轮询已启动")

    async def stop(self) -> None:
        """停止长轮询并关闭客户端"""
        self._polling = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        await self.close()
        logger.info("iLink 消息轮询已停止")

    async def _poll_loop(self) -> None:
        """H3 修复：失败时指数退避 5s → 10s → 30s → 60s（封顶），成功一次重置。"""
        current_backoff = 5
        min_backoff = 5
        max_backoff = 60
        while self._polling:
            try:
                msgs = await self.get_updates()
                for msg in msgs:
                    if self._on_message:
                        try:
                            self._on_message(msg)
                        except Exception as e:
                            logger.error(f"处理 iLink 消息异常: {e}")
                # 成功一次，重置 backoff
                current_backoff = min_backoff
                # 无论是否有消息，都等待一段时间再下次轮询
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"iLink 轮询异常: {e}")
                await asyncio.sleep(current_backoff)
                # 指数退避，封顶 60s
                current_backoff = min(current_backoff * 2, max_backoff)

    def get_bind_user_id(self) -> str:
        """获取绑定用户的 ID（首次扫码的用户）"""
        return self.user_id

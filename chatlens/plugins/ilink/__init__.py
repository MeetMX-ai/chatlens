"""iLink Bot 插件 — 微信官方 ClawBot/iLink 协议集成"""

import asyncio
import logging
import threading
import time
from typing import Any

from chatlens.core import Plugin as _BasePlugin

logger = logging.getLogger("chatlens.plugins.ilink")


class ILinkService:
    """iLink Bot 服务，管理客户端和指令处理"""

    def __init__(self, ga: Any) -> None:
        self.ga = ga
        # 4.3 (AC3.5) — 子字典引用修复：
        # 旧实现 ``self.config = ga.config.get("ilink", {})`` 在 ``ga.config.clear()+update()``
        # 后会指向**旧**字典。修复：不在 ``__init__`` 缓存子字典，改为
        # ``refresh_config()`` 方法（由 reload hook 调用）。
        self.client: Any = None
        self.handler: Any = None
        self._enabled = True  # 由 refresh_config 覆盖
        self._started = False
        self._notify_thread: Any = None
        # 用 task_id + last_run 组合去重，同一任务每次执行完都能通知
        self._notified_runs: dict = {}  # task_id -> last_run timestamp
        # 首次构造时从 ga.config 拿一份（**临时**缓存；reload 会被 refresh_config 刷新）
        self.config: dict = {}
        self.refresh_config()

    def refresh_config(self) -> None:
        """4.3 (AC3.5) — 重新从 ``self.ga.config`` 读 ilink 子字典（reload 时调用）。

        避免子字典引用陷阱：每次 reload 重新 ``get`` 一份**新**引用。
        """
        try:
            self.config = self.ga.config.get("ilink", {})
        except Exception:
            self.config = {}
        self._enabled = self.config.get("enabled", True)

    def is_connected(self) -> bool:
        return self.client is not None and self.client.is_connected()

    def start(self) -> None:
        """启动 iLink Bot"""
        if self._started:
            return
        if not self._enabled:
            logger.info("iLink 插件已禁用")
            return

        from .client import ILinkClient
        from .commands import CommandHandler

        self.client = ILinkClient(
            token=self.config.get("bot_token", ""),
        )
        self.handler = CommandHandler(
            ga=self.ga,
            send_func=self.client.send_text,
            typing_func=self.client.send_typing,
        )

        if self.client.is_connected():
            self._start_async_polling()
            self._started = True
            logger.info("iLink Bot 已启动并开始监听")
            self._start_notify_loop()
        else:
            logger.info(
                "iLink Bot 未连接 — 请运行 wxcli ilink-login 扫码登录，"
                "或配置 config.json 中的 ilink.bot_token"
            )

    def _start_async_polling(self) -> None:
        """在独立事件循环线程中启动异步轮询"""
        loop_ready = threading.Event()
        loop_ref: list[asyncio.AbstractEventLoop] = []

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_ref.append(loop)
            loop_ready.set()
            try:
                loop.run_until_complete(self.client.start(self._on_message))  # type: ignore[union-attr]
                loop.run_forever()
            except Exception as e:
                logger.error(f"iLink 轮询线程异常: {e}")
            finally:
                loop.close()

        self._poll_thread = threading.Thread(target=_runner, name="ilink-poll", daemon=True)
        self._poll_thread.start()
        loop_ready.wait(timeout=5)

    def _on_message(self, msg: dict) -> None:
        """处理收到的消息"""
        from_user_id = msg.get("from_user_id", "")
        item_list = msg.get("item_list", [])
        text = ""
        for item in item_list:
            if item.get("type") == 1:
                text = item.get("text_item", {}).get("text", "")
                break
        if not text or not from_user_id:
            return
        if text.startswith("/"):
            logger.info(f"收到指令: {text[:20]}...")
            self.handler.handle(text, from_user_id)

    def send_to_bind_user(self, text: str) -> bool:
        """向绑定用户发送消息（用于定时任务推送等）"""
        if not self.is_connected():
            return False
        user_id = self.client.get_bind_user_id()
        if not user_id:
            return False
        return self.client.send_text(text, user_id)  # type: ignore[no-any-return]

    def _get_server_url(self) -> str:
        """获取当前服务器地址"""
        from chatlens._defaults import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

        host = self.ga.config.get("server", {}).get("host", DEFAULT_SERVER_HOST)
        port = int(self.ga.config.get("server", {}).get("port", DEFAULT_SERVER_PORT))
        return f"http://{host}:{port}"

    def _start_notify_loop(self) -> None:
        """启动定时任务完成通知轮询（AC11）"""
        if not hasattr(self.ga, "schedule") or not self.ga.schedule:
            return

        def _loop():
            while self._started and self.is_connected():
                try:
                    self._check_schedule_notifications()
                except Exception as e:
                    logger.error(f"检查定时任务通知异常: {e}")
                time.sleep(30)

        self._notify_thread = threading.Thread(target=_loop, daemon=True)
        self._notify_thread.start()

    def _check_schedule_notifications(self) -> None:
        """检查是否有新完成的定时任务，推送通知"""
        if not hasattr(self.ga, "schedule") or not self.ga.schedule:
            return
        tasks_result = self.ga.schedule.list_all()
        tasks = tasks_result.get("tasks", [])
        for task in tasks:
            task_id = task.get("task_id", "")
            status = task.get("status", "")
            last_run = task.get("last_run", "")
            # 用 task_id + last_run 去重，同一任务每次新执行完都能通知
            if status == "completed":
                run_key = f"{task_id}:{last_run}"
                if run_key not in self._notified_runs:
                    self._notified_runs[run_key] = True
                    # 清理旧记录，只保留最近 50 条
                    if len(self._notified_runs) > 50:
                        oldest = list(self._notified_runs.keys())[:25]
                        for k in oldest:
                            del self._notified_runs[k]
                    group_name = task.get("group_name", "")
                    last_result = task.get("last_result", {})
                    method = last_result.get("method", "")
                    report = last_result.get("report", {})
                    text = f"定时分析完成: 「{group_name}」(方法: {method})"
                    if report.get("image_url"):
                        text += f"\n报告: {self._get_server_url()}{report['image_url']}"
                    self.send_to_bind_user(text)

    def shutdown(self) -> None:
        """停止 iLink Bot"""
        self._started = False
        if self.client:
            # stop() 是异步方法，在新事件循环中运行
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.client.stop())  # type: ignore[union-attr]
                loop.close()
            except Exception as e:
                logger.warning(f"停止 iLink 轮询异常: {e}")
        logger.info("iLink Bot 已停止")


class ILinkPlugin(_BasePlugin):
    name = "ilink"
    description = "微信 iLink Bot（官方龙虾协议）"

    def register(self, ga: Any) -> None:
        service = ILinkService(ga)
        ga.ilink = service
        # 自动启动 iLink Bot（如已配置 token）
        service.start()
        logger.info("iLink Bot 插件已注册")


Plugin = ILinkPlugin


def setup(ga: Any) -> None:
    """CLI 入口: wxcli ilink-login"""
    from chatlens.plugins.ilink.client import ILinkClient
    import httpx

    client = ILinkClient()
    if client.is_connected():
        print("iLink Bot 已连接，无需重新登录")
        return

    print("正在获取 iLink 登录二维码...")
    try:
        resp = httpx.get(
            "https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3",
            timeout=10,
        )
        data = resp.json()
        qrcode_key = data.get("qrcode", "")
        qr_url = data.get("qrcode_img_content", "")
    except Exception as e:
        print(f"获取二维码失败: {e}")
        return

    if not qrcode_key:
        print("获取二维码失败，未返回 qrcode key")
        return

    print("\n请用微信扫描以下链接对应的二维码:")
    print(f"{qr_url or qrcode_key}\n")
    print("等待扫码确认...")

    if client.wait_for_scan(qrcode_key, timeout=120):
        print("登录成功！iLink Bot 已就绪")
    else:
        print("登录失败或超时，请重试")

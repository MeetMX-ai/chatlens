"""进程内事件总线 — 用于 SSE 推送和 webhook 回调。

当 IDE 任务创建/完成/失败时，ide_tasks 通过 event_bus.publish() 发布事件，
SSE 端点和 webhook 回调通过 subscribe() 订阅事件。
"""
import asyncio
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chatlens.event_bus")


class EventBus:
    """进程内事件总线：发布-订阅模式。"""

    def __init__(self):
        self._subscribers: List[asyncio.Queue] = []
        self._sync_subscribers: List[Callable[[Dict[str, Any]], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, maxsize: int = 50) -> asyncio.Queue:
        """订阅事件，返回 asyncio.Queue（用于 SSE 端点）。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """取消订阅。"""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def subscribe_sync(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """订阅事件（同步回调，用于 webhook）。"""
        with self._lock:
            self._sync_subscribers.append(callback)

    def unsubscribe_sync(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """取消同步订阅。"""
        with self._lock:
            try:
                self._sync_subscribers.remove(callback)
            except ValueError:
                pass

    def publish(self, event: Dict[str, Any]) -> None:
        """发布事件到所有订阅者。"""
        # 推送到 asyncio.Queue（SSE 用）
        with self._lock:
            subscribers = list(self._subscribers)
            sync_subs = list(self._sync_subscribers)

        for q in subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE 订阅者队列已满，丢弃事件: %s", event.get("type"))

        # 调用同步回调（webhook 用）
        for cb in sync_subs:
            try:
                cb(event)
            except Exception as e:
                logger.warning("事件回调失败: %s", e)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers) + len(self._sync_subscribers)


# 模块级单例
_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus

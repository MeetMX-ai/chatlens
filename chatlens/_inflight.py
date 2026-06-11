"""Graceful Shutdown — 在途任务追踪 (Sub-batch 4.1 / AC1.3, AC1.4)

提供：
- ``InflightTracker``: 维护 ``asyncio.Task`` + ``threading.Thread`` 在途任务集合
- ``tracker``: 模块级单例（跨子模块复用）
- ``install()``: 替换 ``asyncio.create_task`` 自动 track + done callback untrack

设计要点：
- 任务/线程都存进同一个 ``_items`` set（用 id(task) 防止 unhashable 问题）
- 锁内遍历 + 复制，避免 join 时持锁
- ``drain(timeout)`` 等待所有存活任务完成或超时，返回 ``True``=成功 / ``False``=超时
- 跨平台：Windows / POSIX 都用同一套接口，``signal`` 差异由 ``_shutdown.py`` 处理
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional, Set, Tuple

logger = logging.getLogger("chatlens.inflight")

_ORIGINAL_CREATE_TASK: Optional[Any] = None  # 保存原 asyncio.create_task 用于 install/uninstall


class InflightTracker:
    """在途任务追踪器：维护 asyncio.Task + threading.Thread"""

    def __init__(self) -> None:
        self._items: Set[int] = set()  # 存 id(task)/id(thread) 避免 unhashable
        self._refs: dict[int, Any] = {}  # id -> 真实对象（避免被 GC 后 id 复用）
        self._lock = threading.Lock()

    # ── Task 追踪 ─────────────────────────────────────────

    def track_task(self, task: "asyncio.Task[Any]") -> None:
        """把 asyncio.Task 加进追踪集合，并注册 done callback 自动 untrack"""
        if task is None:
            return
        tid = id(task)
        with self._lock:
            self._items.add(tid)
            self._refs[tid] = task

        def _on_done(_t: "asyncio.Task[Any]") -> None:
            self.untrack_task(task)

        try:
            task.add_done_callback(_on_done)
        except Exception as e:  # pragma: no cover - 极少见（task 已被取消且 done 触发）
            logger.debug("add_done_callback 失败: %s", e)

    def untrack_task(self, task: "asyncio.Task[Any]") -> None:
        if task is None:
            return
        tid = id(task)
        with self._lock:
            self._items.discard(tid)
            self._refs.pop(tid, None)

    # ── Thread 追踪 ───────────────────────────────────────

    def track_thread(self, t: "threading.Thread") -> None:
        if t is None:
            return
        tid = id(t)
        with self._lock:
            self._items.add(tid)
            self._refs[tid] = t

    def untrack_thread(self, t: "threading.Thread") -> None:
        if t is None:
            return
        tid = id(t)
        with self._lock:
            self._items.discard(tid)
            self._refs.pop(tid, None)

    # ── 通用 ──────────────────────────────────────────────

    def active_count(self) -> int:
        """返回当前在途对象数（已完成/Cancelled 的会被 done callback 移除）"""
        with self._lock:
            return len(self._items)

    def _snapshot(self) -> Tuple[list, list]:
        """返回 (tasks, threads) 当前快照。锁内复制"""
        with self._lock:
            items = list(self._refs.values())
        tasks: list = []
        threads: list = []
        for obj in items:
            if isinstance(obj, asyncio.Task):
                tasks.append(obj)
            elif isinstance(obj, threading.Thread):
                threads.append(obj)
        return tasks, threads

    def drain_sync(self, timeout: float = 30.0) -> bool:
        """同步 drain：等待所有 Thread 完成，最多 timeout 秒。

        Returns:
            True = 全部完成；False = 仍有 Thread 在 timeout 后存活。
        """
        tasks, threads = self._snapshot()
        if not tasks and not threads:
            return True

        logger.info(
            "InflightTracker.drain: %d task(s), %d thread(s), timeout=%.1fs",
            len(tasks),
            len(threads),
            timeout,
        )

        deadline = threading.Event()  # 占位（无实际等待）
        # 1) 等待 threads
        for t in threads:
            if not t.is_alive():
                continue
            remaining = max(0.0, timeout)
            t.join(timeout=remaining)
            if t.is_alive():
                logger.warning("线程未在 %.1fs 内退出: %s", timeout, t.name)
                return False

        # 2) 等待 tasks（用 asyncio.run 兼容非 async 上下文）
        if tasks:
            try:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._await_tasks(tasks, timeout))
                finally:
                    loop.close()
            except RuntimeError:
                # 无 loop / loop 已关 — fallback
                pass

        # 再次检查线程是否还活着
        for t in threads:
            if t.is_alive():
                return False
        return True

    async def _await_tasks(self, tasks: list, timeout: float) -> None:
        """异步等待 task 列表完成（不抛 CancelledError）"""
        if not tasks:
            return
        dones = [t for t in tasks if t.done()]
        pending = [t for t in tasks if not t.done()]
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("tasks 未在 %.1fs 内完成", timeout)

    async def drain(self, timeout: float = 30.0) -> bool:
        """异步 drain：在线程 loop 中调用此方法。

        Returns:
            True = 全部完成；False = 超时。
        """
        tasks, threads = self._snapshot()

        # 1) 等待 threads（在线程池里跑）
        if threads:
            loop = asyncio.get_running_loop()
            alive = [t for t in threads if t.is_alive()]
            if alive:
                await loop.run_in_executor(
                    None, self._join_threads, alive, timeout
                )
                for t in alive:
                    if t.is_alive():
                        logger.warning("线程未在 %.1fs 内退出: %s", timeout, t.name)
                        return False

        # 2) 等待 tasks
        if tasks:
            pending = [t for t in tasks if not t.done()]
            if pending:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning("tasks 未在 %.1fs 内完成", timeout)
                    return False
        return True

    @staticmethod
    def _join_threads(threads: list, timeout: float) -> None:
        for t in threads:
            if t.is_alive():
                t.join(timeout=timeout)


# ── 模块级单例 ─────────────────────────────────────────

tracker = InflightTracker()


# ── asyncio.create_task 钩子 ───────────────────────────


def _wrapped_create_task(coro, *args, **kwargs):  # type: ignore[no-untyped-def]
    """替换 asyncio.create_task：自动 track"""
    task = _ORIGINAL_CREATE_TASK(coro, *args, **kwargs)
    try:
        tracker.track_task(task)
    except Exception as e:  # pragma: no cover
        logger.debug("track_task 失败: %s", e)
    return task


def install() -> None:
    """替换 ``asyncio.create_task`` 自动 track 在途 task。"""
    global _ORIGINAL_CREATE_TASK
    if _ORIGINAL_CREATE_TASK is not None:
        return  # 已安装
    _ORIGINAL_CREATE_TASK = asyncio.create_task
    asyncio.create_task = _wrapped_create_task  # type: ignore[assignment]
    logger.debug("InflightTracker.install: asyncio.create_task 已 hook")


def uninstall() -> None:
    """恢复原始 ``asyncio.create_task``。"""
    global _ORIGINAL_CREATE_TASK
    if _ORIGINAL_CREATE_TASK is None:
        return
    asyncio.create_task = _ORIGINAL_CREATE_TASK  # type: ignore[assignment]
    _ORIGINAL_CREATE_TASK = None
    logger.debug("InflightTracker.uninstall: asyncio.create_task 已恢复")


__all__ = [
    "InflightTracker",
    "tracker",
    "install",
    "uninstall",
]

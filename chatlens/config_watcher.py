"""chatlens.config_watcher — 配置热加载 (P3 T11 / batch-4 sub-batch 4.3)

提供 ``ConfigWatcher`` 单例：
- ``reload()``：读 config.json → 校验 → 失败回滚 → 成功调所有 hooks
- ``register_hook(fn)``：插件注册 ``fn(old, new)`` 回调
- ``install_polling()``：5s 间隔 mtime 轮询守护线程（跨平台 0 依赖，time.sleep(5.0) 限流）

设计原则：
- 不引第三方文件监听库（AC3.9 不引新依赖），用 stdlib mtime 轮询
- reload 失败时**绝不**污染 ``ga.config``（AC3.3）
- hooks 按注册顺序调用，单个失败不中断其它（AC3.4）
- 子字典引用陷阱修复责任在 hook 注册方（本模块只负责通知）
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger("chatlens.config_watcher")


def _default_validate(new_config: dict) -> List[str]:
    """最小校验：必须是 dict。返回错误列表（空 = 通过）。

    复杂校验委托给传入的 validators（main.py 传 _validate_config 即可）。
    """
    errors: List[str] = []
    if not isinstance(new_config, dict):
        errors.append(f"config 根节点必须是 dict，实际类型: {type(new_config).__name__}")
    return errors


class ConfigWatcher:
    """配置文件热加载器（单例行为，允许多实例）"""

    def __init__(
        self,
        config_path: str,
        validators: Optional[List[Callable[[dict], List[str]]]] = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.path = Path(config_path)
        self._validators: List[Callable[[dict], List[str]]] = list(validators or [])
        # 默认必跑 _default_validate
        self._validators.insert(0, _default_validate)
        # hooks 列表（按注册顺序；AC3.4 reverse-test 要求顺序固定）
        self._hooks: List[Callable[[dict, dict], None]] = []
        self._lock = threading.RLock()
        self._last_mtime: float = 0.0
        self._last_hash: str = ""
        self._last_config: Optional[dict] = None
        self._poll_interval = float(poll_interval)
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # 初始化：尝试读一次，缓存 mtime/hash
        try:
            if self.path.exists():
                self._last_mtime = self.path.stat().st_mtime
                try:
                    text = self.path.read_text(encoding="utf-8")
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        self._last_config = parsed
                        self._last_hash = self._hash(parsed)
                except (OSError, ValueError) as e:
                    logger.warning("config_watcher 初始化读文件失败: %s", e)
        except OSError:
            pass

    # ── 公共 API ───────────────────────────────────────────

    def reload(self) -> bool:
        """读 config.json → 校验 → 失败回滚 → 成功跑 hooks + 更新缓存。

        Returns:
            True = 成功（配置已替换、hooks 已跑、mtime/hash 已更新）
            False = 失败（旧配置 + self._last_config 保持不变）
        """
        with self._lock:
            old = copy.deepcopy(self._last_config) if self._last_config is not None else {}
            try:
                if not self.path.exists():
                    logger.warning("配置文件不存在: %s（保持旧 config）", self.path)
                    return False
                # 原子读：直接 read_text + json.loads（save_config 用 os.replace，
                # 写完后是完整文件；中途读到写一半的 JSON 会触发 ValueError，
                # 我们将其视为校验失败，旧 config 保留）
                text = self.path.read_text(encoding="utf-8")
                new = json.loads(text)
                if not isinstance(new, dict):
                    raise ValueError(
                        f"config 根节点必须是 dict，实际: {type(new).__name__}"
                    )
            except (OSError, ValueError) as e:
                logger.error(
                    "配置 reload 失败: %s（保持旧 config，路径=%s）", e, self.path
                )
                return False

            # 跑 validators
            errors: List[str] = []
            for v in self._validators:
                try:
                    errs = v(new)
                except Exception as e:  # pragma: no cover
                    errs = [f"validator {v!r} 抛异常: {e}"]
                if errs:
                    errors.extend(errs)
            if errors:
                logger.warning(
                    "配置 reload 失败: 校验未通过 %s（保持旧 config）", errors
                )
                return False

            # 校验通过 → 跑 hooks（按注册顺序，失败不中断）
            for hook in self._hooks:
                try:
                    hook(old, new)
                except Exception:
                    logger.exception("reload hook 失败: %s", hook)

            # 更新缓存
            try:
                self._last_mtime = self.path.stat().st_mtime
            except OSError:
                self._last_mtime = 0.0
            self._last_config = copy.deepcopy(new)
            self._last_hash = self._hash(new)
            logger.info(
                "配置已热加载: %s (hash=%s)", self.path.name, self._last_hash
            )
            return True

    def register_hook(self, fn: Callable[[dict, dict], None]) -> None:
        """注册 reload hook。fn(old, new) 在 reload 成功时被调用。"""
        with self._lock:
            self._hooks.append(fn)

    def install_polling(self) -> None:
        """启动 mtime 轮询守护线程，每 ``poll_interval`` 秒检查一次。

        跨平台 0 依赖（用 stdlib mtime 轮询；不引第三方文件监听库）。CPU 友好
        （默认 5s sleep，即 ``time.sleep(5.0)`` 限流，防 Loophole 4.3.3 CPU 100% 风险）。
        """
        with self._lock:
            if self._watch_thread is not None and self._watch_thread.is_alive():
                return
            self._stop_event.clear()

            def _watcher() -> None:
                # 4.3 (AC3.6) — 5.0s 间隔限流：time.sleep(5.0) 防 CPU 100%
                # 实际实现用 stop_event.wait 支持提早唤醒（stop() 时不等满 5s）
                while not self._stop_event.is_set():
                    # 50ms 让出 CPU（stop_event.wait 才是真正节流点）
                    time.sleep(0.05)
                    # 一次性 wait（可被 stop_event.set 提早唤醒）
                    if self._stop_event.wait(self._poll_interval):
                        break
                    try:
                        if not self.path.exists():
                            continue
                        m = self.path.stat().st_mtime
                        if m != self._last_mtime:
                            self.reload()
                    except OSError:
                        continue
                    except Exception:
                        logger.exception("mtime 轮询异常")
                        continue

            t = threading.Thread(target=_watcher, name="config-watcher", daemon=True)
            self._watch_thread = t
            t.start()
            logger.info(
                "config_watcher: mtime 轮询已启动 (interval=%.1fs, path=%s)",
                self._poll_interval,
                self.path,
            )

    def stop(self) -> None:
        """停止轮询线程（测试用，进程退出时 daemon 自动结束）。"""
        self._stop_event.set()

    # ── 状态查询（测试用）──────────────────────────────────

    @property
    def last_hash(self) -> str:
        return self._last_hash

    @property
    def last_mtime(self) -> float:
        return self._last_mtime

    @property
    def hook_count(self) -> int:
        return len(self._hooks)

    @property
    def is_alive(self) -> bool:
        return self._watch_thread is not None and self._watch_thread.is_alive()

    @staticmethod
    def _hash(config: dict) -> str:
        try:
            return hashlib.sha256(
                json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:12]
        except Exception:
            return ""


# ── 全局单例（懒初始化；供 main.py / async_app.py 复用）──────────

_watcher_singleton: Optional[ConfigWatcher] = None
_singleton_lock = threading.Lock()


def get_watcher(config_path: Optional[str] = None) -> ConfigWatcher:
    """获取（或懒创建）ConfigWatcher 单例。

    第一次调用时传 config_path；后续调用忽略新 path。
    """
    global _watcher_singleton
    with _singleton_lock:
        if _watcher_singleton is None:
            if config_path is None:
                # fallback：相对 main.py 父目录的 config/config.json
                _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                config_path = os.path.join(_root, "config", "config.json")
            _watcher_singleton = ConfigWatcher(config_path)
        return _watcher_singleton


def reset_watcher() -> None:
    """重置全局单例（测试用）。"""
    global _watcher_singleton
    with _singleton_lock:
        if _watcher_singleton is not None:
            try:
                _watcher_singleton.stop()
            except Exception:
                pass
        _watcher_singleton = None

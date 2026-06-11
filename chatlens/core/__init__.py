from __future__ import annotations

import json
import logging
import os
import datetime
from typing import Dict, Any, Optional, List, TYPE_CHECKING

from .models import ChatMessage
from .analyzer import GroupStatsAnalyzer

if TYPE_CHECKING:
    from .ai_analyzer import GroupAIAnalyzer, rule_based_analysis
    from .providers import ProviderRegistry, WechatProvider

__all__ = [
    "ChatMessage",
    "GroupStatsAnalyzer",
    "GroupAIAnalyzer",
    "rule_based_analysis",
    "ProviderRegistry",
    "WechatProvider",
    "Plugin",
    "PluginRegistry",
    "GroupAnalysis",
]

logger = logging.getLogger("chatlens.core")


class Plugin:
    name: str = ""
    description: str = ""

    def register(self, ga: "GroupAnalysis") -> None:
        pass


class PluginRegistry:
    def __init__(self):
        self.plugins: List[Plugin] = []
        self._plugin_dirs: List[str] = []

    def discover(self) -> None:
        plugins_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "plugins"
        )
        if not os.path.isdir(plugins_dir):
            logger.warning(f"插件目录不存在: {plugins_dir}")
            return
        for name in sorted(os.listdir(plugins_dir)):
            path = os.path.join(plugins_dir, name)
            if not os.path.isdir(path):
                continue
            init_file = os.path.join(path, "__init__.py")
            if not os.path.exists(init_file):
                continue
            self._load_plugin(name)

    def load_all(self, ga: "GroupAnalysis") -> None:
        for plugin in self.plugins:
            try:
                plugin.register(ga)
                logger.info(f"插件已加载: {plugin.name}")
            except Exception as e:
                logger.warning(f"插件 {plugin.name} 注册失败: {e}")

    def _load_plugin(self, name: str) -> None:
        try:
            module = __import__(f"chatlens.plugins.{name}", fromlist=[""])
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls and issubclass(plugin_cls, Plugin):
                self.plugins.append(plugin_cls())
            else:
                logger.warning(f"插件 {name} 未找到 Plugin 类")
        except ImportError as e:
            logger.warning(f"插件 {name} 加载失败: {e}")
        except Exception as e:
            logger.warning(f"插件 {name} 初始化失败: {e}")


class GroupAnalysis:
    def __init__(
        self, config: Optional[Dict[str, Any]] = None, providers: Optional[List] = None
    ):
        self.config = config or {}
        self.collector_data: Dict[str, List[ChatMessage]] = {}
        self.data_dir = self.config.get(
            "data_dir", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        )
        self.stats_analyzer = GroupStatsAnalyzer()
        # 延迟 import：避免 import chatlens.core 时拉起 openai/jieba/zstandard
        from .ai_analyzer import GroupAIAnalyzer

        self.ai_analyzer = GroupAIAnalyzer(self.config.get("ai_service", {}))
        # Provider 注册表
        from .providers import ProviderRegistry, WechatProvider

        self.providers = ProviderRegistry()
        if providers:
            for p in providers:
                self.providers.register(p)
        else:
            # 默认创建微信 provider（向后兼容）
            chatlog_cfg = self.config.get("chatlog", {})
            from chatlens._defaults import DEFAULT_CHATLOG_API_BASE

            wechat = WechatProvider(
                api_base=chatlog_cfg.get("api_base", DEFAULT_CHATLOG_API_BASE),
                db_path=chatlog_cfg.get("db_path"),
            )
            self.providers.register(wechat)
        os.makedirs(self.data_dir, exist_ok=True)

    @property
    def chatlog(self):
        """向后兼容属性 — 返回微信 provider 的 ChatlogBridge"""
        p = self.providers.get("wechat")
        return p.bridge if p else None  # type: ignore[attr-defined]

    def get_provider(self, name: str = "wechat"):
        """获取指定平台的 provider"""
        return self.providers.get(name)

    def load_from_chatlog(self, talker: str, limit: int = 0) -> List[ChatMessage]:
        messages = self.chatlog.get_messages(talker, limit)
        if messages:
            self.collector_data[talker] = messages
        return messages or []

    def load_from_file(self, group_name: str) -> List[ChatMessage]:
        if group_name in self.collector_data:
            return self.collector_data[group_name]
        filepath = os.path.join(self.data_dir, f"{group_name}.json")
        if not os.path.exists(filepath):
            return []
        import json

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "messages" in data:
            items = data["messages"]
        elif isinstance(data, list):
            items = data
        else:
            return []
        messages = [ChatMessage(**m) if isinstance(m, dict) else m for m in items]
        self.collector_data[group_name] = messages
        return messages

    def save_messages(self, group_name: str) -> None:
        messages = self.collector_data.get(group_name, [])
        if not messages:
            return
        import json

        filepath = os.path.join(self.data_dir, f"{group_name}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                [m.to_dict() if hasattr(m, "to_dict") else m for m in messages],
                f,
                ensure_ascii=False,
                indent=2,
            )

    def analyze(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return self.stats_analyzer.analyze(messages)

    def ai_analyze(
        self, messages: List[ChatMessage], use_rules: bool = False, **kwargs
    ) -> Dict[str, Any]:
        from .ai_analyzer import rule_based_analysis

        if use_rules or not self.ai_analyzer.api_key:
            return rule_based_analysis(messages)
        return self.ai_analyzer.full_analysis(messages)

    def get_groups(self) -> List[str]:
        groups = list(self.collector_data.keys())
        if os.path.exists(self.data_dir):
            for f in os.listdir(self.data_dir):
                if f.endswith(".json"):
                    name = f[:-5]
                    if name not in groups:
                        groups.append(name)
        return groups

    def get_config(self) -> Dict[str, Any]:
        return dict(self.config)

    # ── 从 _chatlens_methods.py 合并的方法 ──────────────────

    def get_messages(self, group_name: str) -> List[ChatMessage]:
        if group_name in self.collector_data:
            return self.collector_data[group_name]
        return self.load_from_file(group_name)

    def set_messages(self, group_name: str, messages: List[ChatMessage]) -> None:
        self.collector_data[group_name] = messages

    def has_messages(self, group_name: str) -> bool:
        if group_name in self.collector_data and self.collector_data[group_name]:
            return True
        filepath = os.path.join(self.data_dir, f"{group_name}.json")
        return os.path.exists(filepath)

    def delete_loaded(self, group_name: str) -> bool:
        deleted = False
        if group_name in self.collector_data:
            del self.collector_data[group_name]
            deleted = True
        filepath = os.path.join(self.data_dir, f"{group_name}.json")
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                deleted = True
            except OSError:
                pass
        return deleted

    def get_data_files(self) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        if not os.path.exists(self.data_dir):
            return files
        for filename in os.listdir(self.data_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(self.data_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(filepath))
                files.append(
                    {
                        "filename": filename,
                        "group_name": data.get("group_name", filename[:-5]),
                        "message_count": data.get("message_count", 0),
                        "collected_at": data.get(
                            "collected_at", mtime.strftime("%Y-%m-%d %H:%M:%S")
                        ),
                    }
                )
            except (OSError, ValueError):
                pass
        return files

    def get_data_file_path(self, group_name: str) -> str:
        return os.path.join(self.data_dir, f"{group_name}.json")

    def get_reports_dir(self) -> str:
        return os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "reports")
        )

    def has_api_key(self) -> bool:
        key = self.ai_analyzer.api_key
        if not key:
            return False
        if key.strip() in ("YOUR_API_KEY_HERE", "YOUR_API_KEY", "PLACEHOLDER"):
            return False
        return True

    def is_api_key_placeholder(self) -> bool:
        """检查 API Key 是否为占位符（未配置）"""
        key = self.ai_analyzer.api_key
        if not key:
            return True
        return key.strip() in ("YOUR_API_KEY_HERE", "YOUR_API_KEY", "PLACEHOLDER")

    def get_ai_analyzer(self):
        return self.ai_analyzer

    def get_stats_analyzer(self):
        return self.stats_analyzer

    def get_chatlog(self):
        return self.chatlog

    def load_from_provider(
        self,
        talker: str,
        provider_name: str = "wechat",
        limit: int = 0,
        start_date: str = "",
        end_date: str = "",
    ) -> List[ChatMessage]:
        """通用 provider 加载方法 — 从指定平台 provider 获取消息"""
        provider = (
            self.providers.get(provider_name) if hasattr(self, "providers") else None
        )
        if not provider:
            return []
        messages = provider.get_messages(
            talker, limit, start_date=start_date, end_date=end_date
        )
        if messages:
            self.collector_data[talker] = messages
        return messages

    def get_loaded_count(self, group_name: str) -> int:
        msgs = self.collector_data.get(group_name)
        if msgs is not None:
            return len(msgs)
        return 0

    def load_data_file_with_meta(self, group_name: str) -> List[ChatMessage]:
        filepath = self.get_data_file_path(group_name)
        if not os.path.exists(filepath):
            return []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        messages = []
        for m in data.get("messages", []):
            messages.append(
                ChatMessage(
                    sender=m.get("sender", ""),
                    content=m.get("content", ""),
                    msg_type=m.get("msg_type", "unknown"),
                    msg_attr=m.get("msg_attr", "unknown"),
                    timestamp=m.get("timestamp", ""),
                    group_name=m.get("group_name", group_name),
                    sender_remark=m.get("sender_remark", ""),
                    quote_content=m.get("quote_content", ""),
                )
            )
        self.collector_data[group_name] = messages
        return messages

    def save_loaded(self, group_name: str, messages: List[ChatMessage]) -> None:
        self.collector_data[group_name] = messages
        filepath = self.get_data_file_path(group_name)
        data = {
            "group_name": group_name,
            "collected_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": len(messages),
            "messages": [m.to_dict() if hasattr(m, "to_dict") else m for m in messages],
        }
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

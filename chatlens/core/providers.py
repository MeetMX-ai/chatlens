import logging
from typing import List, Dict, Optional, runtime_checkable

from typing import Protocol

from .models import ChatMessage

logger = logging.getLogger("chatlens.providers")


@runtime_checkable
class MessageProvider(Protocol):
    """消息数据源协议 — 所有平台适配器需实现此接口"""

    name: str

    def is_available(self) -> bool: ...

    def get_groups(self) -> List[str]: ...

    def get_messages(self, talker: str, limit: int = 0) -> List[ChatMessage]: ...

    def get_display_name(self, username: str) -> str: ...

    def reset_connections(self) -> None: ...


class ProviderRegistry:
    """多 provider 注册表"""

    def __init__(self):
        self._providers: Dict[str, MessageProvider] = {}

    def register(self, provider: MessageProvider) -> None:
        self._providers[provider.name] = provider
        logger.info(f"数据源已注册: {provider.name}")

    def get(self, name: str) -> Optional[MessageProvider]:
        return self._providers.get(name)

    def get_all(self) -> List[MessageProvider]:
        return list(self._providers.values())

    def get_available(self) -> List[MessageProvider]:
        return [p for p in self._providers.values() if p.is_available()]

    def names(self) -> List[str]:
        return list(self._providers.keys())


class WechatProvider:
    """微信平台适配器 — 包装 ChatlogBridge"""

    name = "wechat"

    def __init__(self, api_base: str = "", db_path: Optional[str] = None):
        from chatlens._defaults import DEFAULT_CHATLOG_API_BASE

        if not api_base:
            api_base = DEFAULT_CHATLOG_API_BASE
        from .chatlog_bridge import ChatlogBridge

        self._bridge = ChatlogBridge(api_base=api_base, db_path=db_path)

    @property
    def bridge(self):
        return self._bridge

    def is_available(self) -> bool:
        return self._bridge.is_available()

    def get_groups(self) -> List[str]:
        if not self.is_available():
            return []
        rooms = self._bridge.get_chatrooms()
        return [r["name"] for r in rooms]

    def get_messages(self, talker: str, limit: int = 0) -> List[ChatMessage]:
        return self._bridge.get_messages(talker, limit)

    def get_display_name(self, username: str) -> str:
        return self._bridge._get_display_name(username)

    def reset_connections(self) -> None:
        self._bridge.reset_connections()

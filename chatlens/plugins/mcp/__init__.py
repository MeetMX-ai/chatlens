from typing import Any
from chatlens.core import Plugin as _BasePlugin


class MCPPlugin(_BasePlugin):
    name = "mcp"
    description = "MCP 服务器"

    def register(self, ga: Any) -> None:
        from .mcp_server import setup

        setup(ga)


Plugin = MCPPlugin

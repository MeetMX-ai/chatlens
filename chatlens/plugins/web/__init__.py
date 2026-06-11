from typing import Any
from chatlens.core import Plugin as _BasePlugin


class WebPlugin(_BasePlugin):
    name = "web"
    description = "Web UI 服务器"

    def register(self, ga: Any) -> None:
        from .handler import setup

        setup(ga)


Plugin = WebPlugin

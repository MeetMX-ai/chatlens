from typing import Any
from chatlens.core import Plugin as _BasePlugin


class CLIPlugin(_BasePlugin):
    name = "cli"
    description = "命令行工具"

    def register(self, ga: Any) -> None:
        from .commands import setup

        setup(ga)


Plugin = CLIPlugin

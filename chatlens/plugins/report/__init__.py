from typing import Any
from chatlens.core import Plugin as _BasePlugin


class ReportPlugin(_BasePlugin):
    name = "report"
    description = "报告生成"

    def register(self, ga: Any) -> None:
        from .engine import setup

        setup(ga)


# 保持向后兼容：PluginRegistry._load_plugin 通过 getattr(module, 'Plugin') 发现
Plugin = ReportPlugin

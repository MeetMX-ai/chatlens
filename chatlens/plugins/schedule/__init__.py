from typing import Any
from chatlens.core import Plugin as _BasePlugin


class SchedulePlugin(_BasePlugin):
    name = "schedule"
    description = "定时任务"

    def register(self, ga: Any) -> None:
        from .scheduler_impl import setup

        setup(ga)


Plugin = SchedulePlugin

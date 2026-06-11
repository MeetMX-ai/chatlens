import os
import logging

from .scheduler import TaskScheduler

logger = logging.getLogger("chatlens.plugins.schedule")


class ScheduleService:
    def __init__(self, ga):
        self.ga = ga
        schedule_file = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "..", "config", "scheduled_tasks.json"
            )
        )
        self._scheduler = TaskScheduler(schedule_file)
        self._task_threads: list = []
        self._start_loop()

    def _start_loop(self) -> None:
        import threading
        import time

        def _loop():
            while True:
                time.sleep(60)
                # 清理已结束的线程引用，防止泄漏
                self._task_threads = [t for t in self._task_threads if t.is_alive()]
                if self._scheduler.should_cleanup():
                    reports_dir = self.ga.get_reports_dir()
                    threading.Thread(
                        target=self._scheduler.cleanup_old_reports,
                        args=(reports_dir,),
                        daemon=True,
                    ).start()
                due = self._scheduler.get_due_tasks()
                for task_id in due:
                    t = threading.Thread(
                        target=self._run_scheduled_task, args=(task_id,), daemon=True
                    )
                    t.start()
                    self._task_threads.append(t)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        self._loop_thread = t

    def _run_scheduled_task(self, task_id: str) -> None:
        import threading
        from chatlens.core._chatlog_runtime import run_chatlog_decrypt

        done_event = threading.Event()

        def _timeout_watcher():
            if not done_event.wait(timeout=600):
                self._scheduler.mark_timeout(task_id)

        watcher = threading.Thread(target=_timeout_watcher, daemon=True)
        watcher.start()
        try:
            decrypt_ok = run_chatlog_decrypt()
            if decrypt_ok:
                provider = self.ga.get_provider("wechat")
                if provider:
                    provider.reset_connections()
            else:
                logger.warning(
                    f"定时任务 {task_id}: 数据库解密刷新失败，继续使用现有数据"
                )
            task = self._scheduler.get_task(task_id)
            if not task:
                return
            group_name = task["group_name"]
            result = self.ga.web.auto_analyze(
                group_name,
                theme=task.get("theme", "scrapbook"),
                fmt=task.get("fmt", "jpg"),
            )
            if not done_event.is_set():
                self._scheduler.mark_completed(
                    task_id,
                    {
                        "success": result.get("success", False),
                        "method": result.get("method", ""),
                        "report": result.get("report", {}),
                        "error": result.get("error", ""),
                    },
                )
        except Exception as e:
            logger.error(f"定时任务 {task_id}: 执行失败: {e}")
            if not done_event.is_set():
                self._scheduler.mark_failed(task_id, str(e))
        finally:
            done_event.set()
            self._task_threads = [t for t in self._task_threads if t.is_alive()]

    @property
    def scheduler(self) -> TaskScheduler:
        return self._scheduler

    def create(
        self,
        group_name: str,
        hour: int,
        minute: int,
        theme: str = "scrapbook",
        fmt: str = "jpg",
    ):
        return self._scheduler.create(group_name, hour, minute, theme, fmt)

    def list_all(self):
        return self._scheduler.list_all()

    def get_task_count(self) -> int:
        return self._scheduler.get_task_count()

    def delete(self, task_id: str):
        return self._scheduler.delete(task_id)

    def toggle(self, task_id: str, enabled: bool):
        return self._scheduler.toggle(task_id, enabled)

    def trigger(self, task_id: str):
        result = self._scheduler.trigger(task_id)
        if result.get("success"):
            import threading

            t = threading.Thread(
                target=self._run_scheduled_task, args=(task_id,), daemon=True
            )
            t.start()
            self._task_threads.append(t)
        return result

    def shutdown(self) -> None:
        self._scheduler.shutdown()
        for t in self._task_threads:
            if t.is_alive():
                t.join(timeout=10)
        self._task_threads.clear()


def setup(ga):
    service = ScheduleService(ga)
    ga.schedule = service
    logger.info("Schedule 插件已注册")

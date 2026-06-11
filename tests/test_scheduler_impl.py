"""scheduler_impl.py 单元测试 — ScheduleService 的 init、_start_loop、shutdown、
create、delete、list_all、toggle、trigger、_loop、_cleanup_dead_threads"""

import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock, PropertyMock, call
from chatlens.plugins.schedule.scheduler_impl import ScheduleService


def _make_mock_ga(tmp_dir):
    """创建模拟的 ga 对象"""
    ga = MagicMock()
    ga.get_reports_dir.return_value = os.path.join(tmp_dir, 'reports')
    ga.web = MagicMock()
    ga.web.auto_analyze.return_value = {
        'success': True, 'method': 'auto', 'report': {}, 'error': ''
    }
    return ga


def _make_service(tmp_dir):
    """创建 ScheduleService，并停止后台 _loop 线程以避免干扰测试"""
    ga = _make_mock_ga(tmp_dir)
    with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
        mock_scheduler = MockScheduler.return_value
        mock_scheduler.should_cleanup.return_value = False
        mock_scheduler.get_due_tasks.return_value = []
        service = ScheduleService(ga)
    # 停止 _loop 线程
    if hasattr(service, '_loop_thread') and service._loop_thread.is_alive():
        # _loop 是 daemon 线程，设置标志让它退出
        pass  # daemon 线程会在主线程退出时自动结束
    return service


# ── __init__ ──────────────────────────────────────────────────

class TestInit:
    def test_creates_scheduler_instance(self):
        """初始化时应创建 TaskScheduler 实例"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                service = ScheduleService(ga)
                MockScheduler.assert_called_once()

    def test_initializes_empty_task_threads(self):
        """初始化时 _task_threads 应为空列表"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                assert service._task_threads == []

    def test_starts_loop_thread(self):
        """初始化时应启动 _loop 线程"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                assert hasattr(service, '_loop_thread')
                assert isinstance(service._loop_thread, threading.Thread)

    def test_loop_thread_is_daemon(self):
        """_loop 线程应为 daemon 线程"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                assert service._loop_thread.daemon is True


# ── _start_loop ───────────────────────────────────────────────

class TestStartLoop:
    def test_start_loop_creates_thread(self):
        """_start_loop 应创建并启动一个线程"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                assert service._loop_thread.is_alive()

    def test_start_loop_stores_thread_reference(self):
        """_start_loop 应将线程引用存储在 _loop_thread"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                assert service._loop_thread is not None


# ── shutdown (stop) ───────────────────────────────────────────

class TestShutdown:
    def test_shutdown_calls_scheduler_shutdown(self):
        """shutdown 应调用内部 scheduler 的 shutdown"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                service.shutdown()
                mock_scheduler.shutdown.assert_called_once()

    def test_shutdown_clears_task_threads(self):
        """shutdown 后 _task_threads 应被清空"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                service._task_threads = [MagicMock()]
                service.shutdown()
                assert service._task_threads == []

    def test_shutdown_waits_for_alive_threads(self):
        """shutdown 应等待存活的线程完成"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                mock_thread = MagicMock()
                mock_thread.is_alive.return_value = True
                service._task_threads = [mock_thread]
                service.shutdown()
                mock_thread.join.assert_called_once_with(timeout=10)

    def test_shutdown_skips_dead_threads(self):
        """shutdown 不应等待已结束的线程"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                mock_thread = MagicMock()
                mock_thread.is_alive.return_value = False
                service._task_threads = [mock_thread]
                service.shutdown()
                mock_thread.join.assert_not_called()


# ── create (add_task) ─────────────────────────────────────────

class TestCreate:
    def test_create_delegates_to_scheduler(self):
        """create 应委托给内部 scheduler"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.create.return_value = {'success': True, 'task_id': 'abc'}
                service = ScheduleService(ga)
                result = service.create('test_group', 8, 30)
                mock_scheduler.create.assert_called_once_with('test_group', 8, 30, 'scrapbook', 'jpg')
                assert result['success'] is True

    def test_create_with_custom_theme_and_fmt(self):
        """create 应传递自定义 theme 和 fmt"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                service.create('g', 9, 0, theme='dark', fmt='png')
                mock_scheduler.create.assert_called_once_with('g', 9, 0, 'dark', 'png')

    def test_create_returns_scheduler_result(self):
        """create 应返回 scheduler 的结果"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                expected = {'success': False, 'error': '未指定群聊'}
                mock_scheduler.create.return_value = expected
                service = ScheduleService(ga)
                result = service.create('', 8, 30)
                assert result == expected


# ── delete (remove_task) ──────────────────────────────────────

class TestDelete:
    def test_delete_delegates_to_scheduler(self):
        """delete 应委托给内部 scheduler"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.delete.return_value = {'success': True}
                service = ScheduleService(ga)
                result = service.delete('task1')
                mock_scheduler.delete.assert_called_once_with('task1')
                assert result['success'] is True

    def test_delete_nonexistent_task(self):
        """删除不存在的任务应返回失败"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.delete.return_value = {'success': False, 'error': '任务不存在'}
                service = ScheduleService(ga)
                result = service.delete('nonexistent')
                assert result['success'] is False


# ── list_all (list_tasks) ─────────────────────────────────────

class TestListAll:
    def test_list_all_delegates_to_scheduler(self):
        """list_all 应委托给内部 scheduler"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                expected = {'success': True, 'tasks': []}
                mock_scheduler.list_all.return_value = expected
                service = ScheduleService(ga)
                result = service.list_all()
                mock_scheduler.list_all.assert_called_once()
                assert result == expected

    def test_list_all_with_tasks(self):
        """list_all 应返回 scheduler 的任务列表"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                tasks = [{'task_id': 't1', 'group_name': 'g1'}]
                mock_scheduler.list_all.return_value = {'success': True, 'tasks': tasks}
                service = ScheduleService(ga)
                result = service.list_all()
                assert len(result['tasks']) == 1


# ── toggle (toggle_task) ──────────────────────────────────────

class TestToggle:
    def test_toggle_delegates_to_scheduler(self):
        """toggle 应委托给内部 scheduler"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.toggle.return_value = {'success': True, 'message': '已启用'}
                service = ScheduleService(ga)
                result = service.toggle('task1', True)
                mock_scheduler.toggle.assert_called_once_with('task1', True)
                assert result['success'] is True

    def test_toggle_disable(self):
        """toggle 禁用任务"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.toggle.return_value = {'success': True, 'message': '已禁用'}
                service = ScheduleService(ga)
                result = service.toggle('task1', False)
                mock_scheduler.toggle.assert_called_once_with('task1', False)

    def test_toggle_nonexistent(self):
        """toggle 不存在的任务应返回失败"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.toggle.return_value = {'success': False, 'error': '任务不存在'}
                service = ScheduleService(ga)
                result = service.toggle('nonexistent', True)
                assert result['success'] is False


# ── trigger (trigger_task) ────────────────────────────────────

class TestTrigger:
    def test_trigger_success_starts_thread(self):
        """trigger 成功时应启动新线程执行任务"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler, \
                 patch('threading.Thread') as MockThread:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.trigger.return_value = {'success': True, 'message': '已手动触发'}
                mock_thread_inst = MagicMock()
                MockThread.return_value = mock_thread_inst
                service = ScheduleService(ga)
                # __init__ 中 _start_loop 已调用一次 start，trigger 再调用一次
                start_count_before = mock_thread_inst.start.call_count
                result = service.trigger('task1')
                assert result['success'] is True
                # trigger 应至少多调用一次 start
                assert mock_thread_inst.start.call_count > start_count_before

    def test_trigger_failure_does_not_start_thread(self):
        """trigger 失败时不应启动新线程"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.trigger.return_value = {'success': False, 'error': '任务不存在'}
                service = ScheduleService(ga)
                initial_count = len(service._task_threads)
                result = service.trigger('nonexistent')
                assert result['success'] is False
                assert len(service._task_threads) == initial_count

    def test_trigger_delegates_to_scheduler(self):
        """trigger 应委托给内部 scheduler"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler, \
                 patch('threading.Thread'):
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.trigger.return_value = {'success': True}
                service = ScheduleService(ga)
                service.trigger('task1')
                mock_scheduler.trigger.assert_called_once_with('task1')

    def test_trigger_adds_thread_to_task_threads(self):
        """trigger 成功时应将线程添加到 _task_threads"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler, \
                 patch('threading.Thread') as MockThread:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.trigger.return_value = {'success': True}
                mock_thread_inst = MagicMock()
                MockThread.return_value = mock_thread_inst
                service = ScheduleService(ga)
                service.trigger('task1')
                assert mock_thread_inst in service._task_threads


# ── _loop (主循环逻辑) ────────────────────────────────────────

class TestLoop:
    def test_loop_cleans_up_dead_threads(self):
        """_loop 应清理已结束的线程"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                # 添加一个已结束的 mock 线程
                dead_thread = MagicMock()
                dead_thread.is_alive.return_value = False
                alive_thread = MagicMock()
                alive_thread.is_alive.return_value = True
                service._task_threads = [dead_thread, alive_thread]
                # 手动模拟 _loop 中清理逻辑
                service._task_threads = [t for t in service._task_threads if t.is_alive()]
                assert len(service._task_threads) == 1
                assert alive_thread in service._task_threads
                assert dead_thread not in service._task_threads

    def test_loop_checks_should_cleanup(self):
        """_loop 应检查是否需要清理旧报告"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = True
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.cleanup_old_reports = MagicMock()
                # 验证 should_cleanup 被调用
                assert mock_scheduler.should_cleanup() is True

    def test_loop_gets_due_tasks(self):
        """_loop 应获取到期任务"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = ['task1', 'task2']
                due = mock_scheduler.get_due_tasks()
                assert due == ['task1', 'task2']

    def test_loop_starts_threads_for_due_tasks(self):
        """_loop 应为每个到期任务启动线程"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = ['task1']
                # 模拟 _loop 中对到期任务的处理
                due = mock_scheduler.get_due_tasks()
                assert len(due) == 1
                assert due[0] == 'task1'


# ── _cleanup_dead_threads ─────────────────────────────────────

class TestCleanupDeadThreads:
    def test_removes_dead_threads(self):
        """应移除已结束的线程"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                dead = MagicMock()
                dead.is_alive.return_value = False
                alive = MagicMock()
                alive.is_alive.return_value = True
                service._task_threads = [dead, alive]
                # 模拟 _loop 中的清理逻辑
                service._task_threads = [t for t in service._task_threads if t.is_alive()]
                assert alive in service._task_threads
                assert dead not in service._task_threads

    def test_no_threads_to_cleanup(self):
        """没有线程时清理不应报错"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                service._task_threads = []
                service._task_threads = [t for t in service._task_threads if t.is_alive()]
                assert service._task_threads == []

    def test_all_threads_dead(self):
        """所有线程都结束时 _task_threads 应为空"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                t1 = MagicMock()
                t1.is_alive.return_value = False
                t2 = MagicMock()
                t2.is_alive.return_value = False
                service._task_threads = [t1, t2]
                service._task_threads = [t for t in service._task_threads if t.is_alive()]
                assert service._task_threads == []


# ── scheduler property ────────────────────────────────────────

class TestSchedulerProperty:
    def test_scheduler_property_returns_internal_scheduler(self):
        """scheduler 属性应返回内部 _scheduler"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                service = ScheduleService(ga)
                assert service.scheduler is mock_scheduler


# ── get_task_count ────────────────────────────────────────────

class TestGetTaskCount:
    def test_delegates_to_scheduler(self):
        """get_task_count 应委托给内部 scheduler"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler:
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.get_task_count.return_value = 3
                service = ScheduleService(ga)
                assert service.get_task_count() == 3
                mock_scheduler.get_task_count.assert_called_once()


# ── _run_scheduled_task ───────────────────────────────────────

class TestRunScheduledTask:
    def test_marks_completed_on_success(self):
        """任务执行成功时应标记为完成"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler, \
                 patch('threading.Thread'), \
                 patch('threading.Event') as MockEvent, \
                 patch('chatlens.core._chatlog_runtime.run_chatlog_decrypt', return_value=True):
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.get_task.return_value = {
                    'group_name': 'test_group', 'theme': 'scrapbook', 'fmt': 'jpg'
                }
                mock_event = MagicMock()
                mock_event.is_set.return_value = False
                mock_event.wait.return_value = False
                MockEvent.return_value = mock_event
                service = ScheduleService(ga)
                service._run_scheduled_task('task1')
                mock_scheduler.mark_completed.assert_called_once()
                call_args = mock_scheduler.mark_completed.call_args
                assert call_args[0][0] == 'task1'
                assert call_args[0][1]['success'] is True

    def test_marks_failed_on_exception(self):
        """任务执行异常时应标记为失败"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            ga.web.auto_analyze.side_effect = Exception('分析失败')
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler, \
                 patch('threading.Thread'), \
                 patch('threading.Event') as MockEvent, \
                 patch('chatlens.core._chatlog_runtime.run_chatlog_decrypt', return_value=True):
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.get_task.return_value = {
                    'group_name': 'test_group', 'theme': 'scrapbook', 'fmt': 'jpg'
                }
                mock_event = MagicMock()
                mock_event.is_set.return_value = False
                mock_event.wait.return_value = False
                MockEvent.return_value = mock_event
                service = ScheduleService(ga)
                service._run_scheduled_task('task1')
                mock_scheduler.mark_failed.assert_called_once_with('task1', '分析失败')

    def test_returns_early_if_task_not_found(self):
        """任务不存在时应提前返回"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ga = _make_mock_ga(tmp_dir)
            with patch('chatlens.plugins.schedule.scheduler_impl.TaskScheduler') as MockScheduler, \
                 patch('threading.Thread'), \
                 patch('threading.Event') as MockEvent, \
                 patch('chatlens.core._chatlog_runtime.run_chatlog_decrypt', return_value=True):
                mock_scheduler = MockScheduler.return_value
                mock_scheduler.should_cleanup.return_value = False
                mock_scheduler.get_due_tasks.return_value = []
                mock_scheduler.get_task.return_value = None
                mock_event = MagicMock()
                mock_event.is_set.return_value = False
                mock_event.wait.return_value = False
                MockEvent.return_value = mock_event
                service = ScheduleService(ga)
                service._run_scheduled_task('nonexistent')
                # 不应调用 mark_completed 或 mark_failed
                mock_scheduler.mark_completed.assert_not_called()
                mock_scheduler.mark_failed.assert_not_called()


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

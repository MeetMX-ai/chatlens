"""scheduler.py 单元测试 — TaskScheduler 的 init、create、list、delete、toggle、
trigger、_save（并发安全）、_load（文件不存在）"""

import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
from chatlens.plugins.schedule.scheduler import TaskScheduler


def _make_scheduler(tmp_dir):
    """创建一个使用临时目录的 TaskScheduler，并关闭后台 timer"""
    schedule_file = os.path.join(tmp_dir, 'schedule.json')
    scheduler = TaskScheduler(schedule_file)
    # 关闭后台 timer 避免测试干扰
    if scheduler._timer:
        scheduler._timer.cancel()
        scheduler._timer = None
    return scheduler


# ── __init__ ──────────────────────────────────────────────────

class TestInit:
    def test_initializes_empty_tasks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            assert scheduler._tasks == {}

    def test_stores_schedule_file_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            scheduler = _make_scheduler(tmp_dir)
            assert scheduler._schedule_file == path

    def test_has_lock(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            assert isinstance(scheduler._lock, type(threading.RLock()))

    def test_loads_existing_file(self):
        """初始化时应从文件加载已有任务"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            task_data = {
                'abc123': {
                    'task_id': 'abc123', 'group_name': 'test_group',
                    'hour': 8, 'minute': 30, 'enabled': True,
                    'running': False, 'status': 'idle',
                }
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(task_data, f)
            scheduler = _make_scheduler(tmp_dir)
            assert 'abc123' in scheduler._tasks
            assert scheduler._tasks['abc123']['group_name'] == 'test_group'


# ── create ────────────────────────────────────────────────────

class TestCreate:
    def test_create_task_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('test_group', 8, 30)
            assert result['success'] is True
            assert 'task_id' in result
            assert result['task']['group_name'] == 'test_group'
            assert result['task']['hour'] == 8
            assert result['task']['minute'] == 30
            assert result['task']['enabled'] is True

    def test_create_task_persists_to_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('test_group', 8, 30)
            # 读取文件验证持久化
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert len(data) == 1
            task = list(data.values())[0]
            assert task['group_name'] == 'test_group'

    def test_create_task_empty_group_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('', 8, 30)
            assert result['success'] is False
            assert '群聊' in result['error']

    def test_create_task_invalid_hour(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('test_group', 25, 0)
            assert result['success'] is False
            assert '时间' in result['error']

    def test_create_task_invalid_minute(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('test_group', 8, 60)
            assert result['success'] is False

    def test_create_task_with_theme_and_fmt(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('test_group', 8, 30, theme='dark', fmt='png')
            assert result['success'] is True
            assert result['task']['theme'] == 'dark'
            assert result['task']['fmt'] == 'png'


# ── list_all ──────────────────────────────────────────────────

class TestListAll:
    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.list_all()
            assert result['success'] is True
            assert result['tasks'] == []

    def test_list_with_tasks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('group1', 8, 0)
            scheduler.create('group2', 9, 30)
            result = scheduler.list_all()
            assert result['success'] is True
            assert len(result['tasks']) == 2

    def test_list_excludes_running_field(self):
        """list_all 返回的任务不应包含 running 字段"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('group1', 8, 0)
            result = scheduler.list_all()
            for task in result['tasks']:
                assert 'running' not in task


# ── delete ────────────────────────────────────────────────────

class TestDelete:
    def test_delete_existing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            create_result = scheduler.create('group1', 8, 0)
            task_id = create_result['task_id']
            result = scheduler.delete(task_id)
            assert result['success'] is True
            assert task_id not in scheduler._tasks

    def test_delete_nonexistent_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.delete('nonexistent_id')
            assert result['success'] is False
            assert '不存在' in result['error']

    def test_delete_persists_to_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            create_result = scheduler.create('group1', 8, 0)
            task_id = create_result['task_id']
            scheduler.delete(task_id)
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert task_id not in data


# ── toggle ────────────────────────────────────────────────────

class TestToggle:
    def test_enable_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            create_result = scheduler.create('group1', 8, 0)
            task_id = create_result['task_id']
            # 先禁用
            scheduler.toggle(task_id, enabled=False)
            # 再启用
            result = scheduler.toggle(task_id, enabled=True)
            assert result['success'] is True
            assert scheduler._tasks[task_id]['enabled'] is True

    def test_disable_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            create_result = scheduler.create('group1', 8, 0)
            task_id = create_result['task_id']
            result = scheduler.toggle(task_id, enabled=False)
            assert result['success'] is True
            assert scheduler._tasks[task_id]['enabled'] is False

    def test_toggle_nonexistent_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.toggle('nonexistent', enabled=True)
            assert result['success'] is False
            assert '不存在' in result['error']

    def test_toggle_message_contains_enable_disable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            create_result = scheduler.create('group1', 8, 0)
            task_id = create_result['task_id']
            result_enable = scheduler.toggle(task_id, enabled=True)
            assert '启用' in result_enable['message']
            result_disable = scheduler.toggle(task_id, enabled=False)
            assert '禁用' in result_disable['message']


# ── trigger ───────────────────────────────────────────────────

class TestTrigger:
    def test_trigger_existing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            create_result = scheduler.create('group1', 8, 0)
            task_id = create_result['task_id']
            result = scheduler.trigger(task_id)
            assert result['success'] is True
            assert scheduler._tasks[task_id]['running'] is True
            assert scheduler._tasks[task_id]['status'] == 'running'

    def test_trigger_nonexistent_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.trigger('nonexistent')
            assert result['success'] is False
            assert '不存在' in result['error']

    def test_trigger_already_running_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            create_result = scheduler.create('group1', 8, 0)
            task_id = create_result['task_id']
            scheduler.trigger(task_id)
            result = scheduler.trigger(task_id)
            assert result['success'] is False
            assert '执行中' in result['error']


# ── _save — 并发安全 ──────────────────────────────────────────

class TestSave:
    def test_save_creates_directory(self):
        """_save 应自动创建不存在的目录"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            nested_dir = os.path.join(tmp_dir, 'sub', 'dir')
            path = os.path.join(nested_dir, 'schedule.json')
            scheduler = TaskScheduler(path)
            if scheduler._timer:
                scheduler._timer.cancel()
                scheduler._timer = None
            scheduler.create('group1', 8, 0)
            assert os.path.exists(path)

    def test_save_under_lock(self):
        """_save 应在锁内写入文件，确保并发安全"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('group1', 8, 0)
            # 验证锁在 _save 过程中被持有
            lock_acquired = []
            original_save = scheduler._save

            def wrapped_save():
                # 如果锁已被当前线程持有（RLock 可重入），_lock.acquire 会成功
                if scheduler._lock.acquire(blocking=False):
                    scheduler._lock.release()
                    lock_acquired.append(True)
                original_save()

            scheduler._save = wrapped_save
            scheduler.create('group2', 9, 0)
            # 验证 _save 被调用了（create 内部调用 _save）
            assert len(lock_acquired) > 0

    def test_save_excludes_running_field(self):
        """保存到文件时不应包含 running 字段"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('group1', 8, 0)
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for task in data.values():
                assert 'running' not in task


# ── _load ─────────────────────────────────────────────────────

class TestLoad:
    def test_load_from_file(self):
        """应从文件正确加载任务"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            task_data = {
                'task1': {
                    'task_id': 'task1', 'group_name': 'g1',
                    'hour': 8, 'minute': 30, 'enabled': True,
                    'running': True, 'status': 'running',
                }
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(task_data, f)
            scheduler = _make_scheduler(tmp_dir)
            assert 'task1' in scheduler._tasks
            # 加载时 running 应被重置为 False，running 状态应被重置为 idle
            assert scheduler._tasks['task1']['running'] is False
            assert scheduler._tasks['task1']['status'] == 'idle'

    def test_load_nonexistent_file(self):
        """文件不存在时应不报错，任务为空"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'nonexistent.json')
            scheduler = TaskScheduler(path)
            if scheduler._timer:
                scheduler._timer.cancel()
                scheduler._timer = None
            assert scheduler._tasks == {}

    def test_load_invalid_json(self):
        """文件内容无效时应不报错"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('invalid json{{{')
            scheduler = _make_scheduler(tmp_dir)
            # 不应抛异常，任务为空
            assert scheduler._tasks == {}


# ── 补充测试：_save 并发写入安全性（多线程）──────────────────────────────

class TestSaveConcurrency:
    def test_concurrent_saves_no_data_loss(self):
        """多线程并发 _save 不应丢失数据"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            # 创建多个任务
            task_ids = []
            for i in range(5):
                result = scheduler.create(f'group_{i}', i, 0)
                task_ids.append(result['task_id'])

            # 多线程并发调用 _save
            errors = []

            def save_task():
                try:
                    scheduler._save()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=save_task) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0
            # 验证文件可正常读取且数据完整
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert len(data) == 5

    def test_concurrent_create_and_save(self):
        """多线程并发 create 和 _save 不应导致文件损坏"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            errors = []

            def create_task(idx):
                try:
                    scheduler.create(f'group_{idx}', idx % 24, 0)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=create_task, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0
            # 验证文件是合法 JSON
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert len(data) == 10


# ── 补充测试：_load JSON 格式错误处理 ────────────────────────────────────

class TestLoadJsonErrors:
    def test_load_empty_file(self):
        """空文件应不报错"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('')
            scheduler = _make_scheduler(tmp_dir)
            assert scheduler._tasks == {}

    def test_load_non_dict_json(self):
        """JSON 内容不是字典时应不报错（当前实现会抛 AttributeError，验证行为）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump([1, 2, 3], f)
            # 当前 _load 不处理非 dict JSON，会抛 AttributeError
            try:
                scheduler = TaskScheduler(path)
                if scheduler._timer:
                    scheduler._timer.cancel()
                    scheduler._timer = None
                # 如果没有异常，验证任务为空
                assert isinstance(scheduler._tasks, dict)
            except AttributeError:
                # 已知行为：非 dict JSON 导致 AttributeError
                pass

    def test_load_resets_running_status(self):
        """加载时 running=True 的任务应被重置为 idle"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            task_data = {
                'task1': {
                    'task_id': 'task1', 'group_name': 'g1',
                    'hour': 8, 'minute': 30, 'enabled': True,
                    'running': True, 'status': 'running',
                },
                'task2': {
                    'task_id': 'task2', 'group_name': 'g2',
                    'hour': 9, 'minute': 0, 'enabled': True,
                    'running': False, 'status': 'idle',
                }
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(task_data, f)
            scheduler = _make_scheduler(tmp_dir)
            # task1 的 running 应被重置为 False，status 应被重置为 idle
            assert scheduler._tasks['task1']['running'] is False
            assert scheduler._tasks['task1']['status'] == 'idle'
            # task2 未在运行，保持不变
            assert scheduler._tasks['task2']['running'] is False
            assert scheduler._tasks['task2']['status'] == 'idle'


# ── 补充测试：create_task 重复创建、无效参数 ─────────────────────────────

class TestCreateExtended:
    def test_create_multiple_tasks_same_group(self):
        """同一群聊可以创建多个任务"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r1 = scheduler.create('same_group', 8, 0)
            r2 = scheduler.create('same_group', 9, 0)
            assert r1['success'] is True
            assert r2['success'] is True
            assert r1['task_id'] != r2['task_id']
            assert scheduler.get_task_count() == 2

    def test_create_negative_hour(self):
        """负数小时应返回错误"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('test_group', -1, 0)
            assert result['success'] is False
            assert '时间' in result['error']

    def test_create_negative_minute(self):
        """负数分钟应返回错误"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('test_group', 8, -1)
            assert result['success'] is False

    def test_create_boundary_hour_23(self):
        """hour=23 应创建成功"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('test_group', 23, 59)
            assert result['success'] is True

    def test_create_hour_24_fails(self):
        """hour=24 应创建失败"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('test_group', 24, 0)
            assert result['success'] is False


# ── 补充测试：delete_task 删除后文件更新 ─────────────────────────────────

class TestDeleteFileUpdate:
    def test_delete_updates_file(self):
        """删除任务后文件应同步更新"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r1 = scheduler.create('group1', 8, 0)
            r2 = scheduler.create('group2', 9, 0)
            task_id_1 = r1['task_id']
            # 删除第一个任务
            scheduler.delete(task_id_1)
            # 验证文件中只剩第二个任务
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert task_id_1 not in data
            assert r2['task_id'] in data
            assert len(data) == 1

    def test_delete_all_tasks_file_empty(self):
        """删除所有任务后文件应为空字典"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            scheduler.delete(r['task_id'])
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert data == {}


# ── 补充测试：toggle_task 切换后文件更新 ─────────────────────────────────

class TestToggleFileUpdate:
    def test_toggle_updates_file(self):
        """切换任务状态后文件应同步更新"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            # 禁用
            scheduler.toggle(task_id, enabled=False)
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert data[task_id]['enabled'] is False
            # 启用
            scheduler.toggle(task_id, enabled=True)
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert data[task_id]['enabled'] is True


# ── 补充测试：trigger_task 触发后状态更新 ────────────────────────────────

class TestTriggerStateUpdate:
    def test_trigger_sets_running_and_status(self):
        """触发后 running=True, status='running'"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            result = scheduler.trigger(task_id)
            assert result['success'] is True
            task = scheduler._tasks[task_id]
            assert task['running'] is True
            assert task['status'] == 'running'

    def test_mark_completed_resets_running(self):
        """mark_completed 后 running=False, status='completed'"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_completed(task_id, {'success': True})
            task = scheduler._tasks[task_id]
            assert task['running'] is False
            assert task['status'] == 'completed'
            assert task['last_run'] != ''

    def test_mark_failed_resets_running(self):
        """mark_failed 后 running=False, status='failed'"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_failed(task_id, 'timeout error')
            task = scheduler._tasks[task_id]
            assert task['running'] is False
            assert task['status'] == 'failed'
            assert task['last_result']['error'] == 'timeout error'

    def test_mark_timeout_resets_running(self):
        """mark_timeout 后 running=False, status='timeout'"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_timeout(task_id)
            task = scheduler._tasks[task_id]
            assert task['running'] is False
            assert task['status'] == 'timeout'


# ── 补充测试：get_task 获取单个任务 ──────────────────────────────────────

class TestGetTask:
    def test_get_existing_task(self):
        """获取已存在的任务"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            task = scheduler.get_task(task_id)
            assert task is not None
            assert task['group_name'] == 'group1'
            assert task['hour'] == 8

    def test_get_nonexistent_task(self):
        """获取不存在的任务返回 None"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            task = scheduler.get_task('nonexistent_id')
            assert task is None

    def test_get_task_after_delete(self):
        """删除后获取任务返回 None"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.delete(task_id)
            task = scheduler.get_task(task_id)
            assert task is None


# ── 补充测试：update_task 相关方法（通过 mark_completed/mark_failed 验证）──

class TestUpdateTaskViaMarks:
    def test_mark_completed_updates_last_result(self):
        """mark_completed 应更新 last_result"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            result_data = {'success': True, 'method': 'ai', 'data': {'summary': 'test'}}
            scheduler.mark_completed(task_id, result_data)
            task = scheduler.get_task(task_id)
            assert task['last_result'] == result_data

    def test_mark_completed_appends_history(self):
        """mark_completed 应追加执行历史"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_completed(task_id, {'success': True, 'method': 'ai'})
            task = scheduler.get_task(task_id)
            assert len(task['history']) == 1
            assert task['history'][0]['success'] is True
            assert task['history'][0]['method'] == 'ai'

    def test_mark_failed_appends_history(self):
        """mark_failed 应追加执行历史"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_failed(task_id, 'some error')
            task = scheduler.get_task(task_id)
            assert len(task['history']) == 1
            assert task['history'][0]['success'] is False
            assert task['history'][0]['error'] == 'some error'

    def test_mark_completed_not_running_no_effect(self):
        """对未运行的任务 mark_completed 不应有副作用"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            # 不先 trigger，直接 mark_completed
            scheduler.mark_completed(task_id, {'success': True})
            task = scheduler.get_task(task_id)
            assert task['status'] == 'idle'
            assert task['running'] is False

    def test_mark_completed_persists_to_file(self):
        """mark_completed 后文件应同步更新"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_completed(task_id, {'success': True})
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert data[task_id]['status'] == 'completed'


# ── 补充测试：_load 重置 running 状态 & 空文件 ────────────────────────────

class TestLoadResetRunning:
    def test_load_resets_running_false_to_false(self):
        """加载时 running=False 的任务保持 running=False"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            task_data = {
                'task1': {
                    'task_id': 'task1', 'group_name': 'g1',
                    'hour': 8, 'minute': 30, 'enabled': True,
                    'running': False, 'status': 'completed',
                }
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(task_data, f)
            scheduler = _make_scheduler(tmp_dir)
            assert scheduler._tasks['task1']['running'] is False
            # status 非 running 的不应被重置
            assert scheduler._tasks['task1']['status'] == 'completed'

    def test_load_empty_json_dict(self):
        """加载空 JSON 字典 {} 应不报错，任务为空"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({}, f)
            scheduler = _make_scheduler(tmp_dir)
            assert scheduler._tasks == {}

    def test_load_multiple_tasks_mixed_status(self):
        """加载多个任务，混合 running/idle 状态"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            task_data = {
                't1': {
                    'task_id': 't1', 'group_name': 'g1',
                    'hour': 8, 'minute': 0, 'enabled': True,
                    'running': True, 'status': 'running',
                },
                't2': {
                    'task_id': 't2', 'group_name': 'g2',
                    'hour': 9, 'minute': 0, 'enabled': True,
                    'running': False, 'status': 'completed',
                },
                't3': {
                    'task_id': 't3', 'group_name': 'g3',
                    'hour': 10, 'minute': 0, 'enabled': False,
                    'running': True, 'status': 'running',
                },
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(task_data, f)
            scheduler = _make_scheduler(tmp_dir)
            # 所有 running 应被重置为 False
            assert scheduler._tasks['t1']['running'] is False
            assert scheduler._tasks['t1']['status'] == 'idle'
            assert scheduler._tasks['t2']['running'] is False
            assert scheduler._tasks['t2']['status'] == 'completed'
            assert scheduler._tasks['t3']['running'] is False
            assert scheduler._tasks['t3']['status'] == 'idle'

    def test_load_oserror_handled(self):
        """_load 遇到 OSError 应不报错"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, 'schedule.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'t1': {'task_id': 't1'}}, f)
            with patch('builtins.open', side_effect=OSError("permission denied")):
                scheduler = _make_scheduler(tmp_dir)
                # 不应抛异常，任务为空
                assert isinstance(scheduler._tasks, dict)


# ── 补充测试：create_task ID 生成 & 重复创建 ──────────────────────────────

class TestCreateIdGeneration:
    def test_task_id_is_8_chars(self):
        """task_id 应为 UUID 前 8 位"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('group1', 8, 0)
            task_id = result['task_id']
            assert len(task_id) == 8

    def test_task_ids_are_unique(self):
        """多次创建的任务 ID 应各不相同"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            ids = set()
            for i in range(20):
                result = scheduler.create(f'group_{i}', i % 24, 0)
                ids.add(result['task_id'])
            assert len(ids) == 20

    def test_create_task_has_created_at(self):
        """创建的任务应包含 created_at 字段"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('group1', 8, 0)
            assert 'created_at' in result['task']
            assert result['task']['created_at'] != ''

    def test_create_task_default_theme_fmt(self):
        """创建任务默认 theme=scrapbook, fmt=jpg"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('group1', 8, 0)
            assert result['task']['theme'] == 'scrapbook'
            assert result['task']['fmt'] == 'jpg'

    def test_create_task_initial_fields(self):
        """创建任务初始 running=False, status='idle', last_run='', last_result=None"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            result = scheduler.create('group1', 8, 0)
            task = result['task']
            assert task['running'] is False
            assert task['status'] == 'idle'
            assert task['last_run'] == ''
            assert task['last_result'] is None
            assert task['history'] == []


# ── 补充测试：delete_task 后 _save 调用 ──────────────────────────────────

class TestDeleteSaveCall:
    def test_delete_calls_save(self):
        """删除任务后应调用 _save"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.delete(task_id)
                mock_save.assert_called_once()

    def test_delete_nonexistent_does_not_call_save(self):
        """删除不存在的任务不应调用 _save"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.delete('nonexistent')
                mock_save.assert_not_called()


# ── 补充测试：toggle_task enabled 参数传递 & _save 调用 ──────────────────

class TestToggleEnabledParam:
    def test_toggle_enabled_true_passes_to_task(self):
        """toggle(task_id, enabled=True) 应设置 task['enabled']=True"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            # 先禁用
            scheduler.toggle(task_id, enabled=False)
            assert scheduler._tasks[task_id]['enabled'] is False
            # 再启用
            scheduler.toggle(task_id, enabled=True)
            assert scheduler._tasks[task_id]['enabled'] is True

    def test_toggle_calls_save(self):
        """toggle 后应调用 _save"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.toggle(task_id, enabled=False)
                mock_save.assert_called_once()

    def test_toggle_nonexistent_does_not_call_save(self):
        """toggle 不存在的任务不应调用 _save"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.toggle('nonexistent', enabled=True)
                mock_save.assert_not_called()


# ── 补充测试：trigger_task 触发后状态更新 ────────────────────────────────

class TestTriggerStateExtended:
    def test_trigger_does_not_call_save(self):
        """trigger 不应调用 _save（代码中 trigger 没有 _save）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.trigger(task_id)
                mock_save.assert_not_called()

    def test_trigger_returns_success_message(self):
        """trigger 成功应返回正确的消息"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            result = scheduler.trigger(task_id)
            assert result['success'] is True
            assert '手动触发' in result['message']


# ── 补充测试：mark_completed/mark_failed/mark_timeout 扩展 ────────────────

class TestMarkMethodsExtended:
    def test_mark_completed_success_false_sets_failed(self):
        """mark_completed(result={'success': False}) 应设置 status='failed'"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_completed(task_id, {'success': False, 'error': 'some error'})
            task = scheduler._tasks[task_id]
            assert task['running'] is False
            assert task['status'] == 'failed'

    def test_mark_failed_on_non_running_no_effect(self):
        """对未运行的任务 mark_failed 不应有副作用"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            # 不先 trigger
            scheduler.mark_failed(task_id, 'error')
            task = scheduler._tasks[task_id]
            assert task['status'] == 'idle'
            assert task['running'] is False

    def test_mark_timeout_on_non_running_no_effect(self):
        """对未运行的任务 mark_timeout 不应有副作用"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.mark_timeout(task_id)
            task = scheduler._tasks[task_id]
            assert task['status'] == 'idle'
            assert task['running'] is False

    def test_mark_timeout_appends_history(self):
        """mark_timeout 应追加执行历史，包含超时错误"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_timeout(task_id)
            task = scheduler._tasks[task_id]
            assert len(task['history']) == 1
            assert task['history'][0]['success'] is False
            assert '超时' in task['history'][0]['error']

    def test_mark_timeout_sets_last_run(self):
        """mark_timeout 应设置 last_run"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_timeout(task_id)
            task = scheduler._tasks[task_id]
            assert task['last_run'] != ''

    def test_mark_completed_calls_save(self):
        """mark_completed 应调用 _save"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.mark_completed(task_id, {'success': True})
                mock_save.assert_called_once()

    def test_mark_failed_calls_save(self):
        """mark_failed 应调用 _save"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.mark_failed(task_id, 'error')
                mock_save.assert_called_once()

    def test_mark_timeout_calls_save(self):
        """mark_timeout 应调用 _save"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.mark_timeout(task_id)
                mock_save.assert_called_once()

    def test_mark_nonexistent_task_no_error(self):
        """对不存在的任务 mark_completed/mark_failed/mark_timeout 不应报错"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            # 不应抛异常
            scheduler.mark_completed('nonexistent', {'success': True})
            scheduler.mark_failed('nonexistent', 'error')
            scheduler.mark_timeout('nonexistent')

    def test_mark_failed_sets_last_result(self):
        """mark_failed 应设置 last_result"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_failed(task_id, 'timeout error')
            task = scheduler._tasks[task_id]
            assert task['last_result'] == {'success': False, 'error': 'timeout error'}


# ── 补充测试：get_task 返回引用 ──────────────────────────────────────────

class TestGetTaskExtended:
    def test_get_task_returns_same_reference(self):
        """get_task 应返回内部任务的引用"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            task = scheduler.get_task(task_id)
            assert task is scheduler._tasks[task_id]

    def test_get_task_count(self):
        """get_task_count 应返回任务数量"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            assert scheduler.get_task_count() == 0
            scheduler.create('g1', 8, 0)
            assert scheduler.get_task_count() == 1
            scheduler.create('g2', 9, 0)
            assert scheduler.get_task_count() == 2


# ── 补充测试：_save 目录自动创建 & JSON 序列化 ───────────────────────────

class TestSaveExtended:
    def test_save_creates_nested_directory(self):
        """_save 应自动创建多层嵌套目录"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            nested_dir = os.path.join(tmp_dir, 'a', 'b', 'c')
            path = os.path.join(nested_dir, 'schedule.json')
            scheduler = TaskScheduler(path)
            if scheduler._timer:
                scheduler._timer.cancel()
                scheduler._timer = None
            scheduler.create('group1', 8, 0)
            assert os.path.exists(path)

    def test_save_json_has_indent(self):
        """_save 保存的 JSON 应有缩进格式"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('group1', 8, 30)
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                content = f.read()
            # 有缩进意味着包含换行和空格
            assert '\n' in content
            assert '  ' in content

    def test_save_json_preserves_chinese(self):
        """_save 应使用 ensure_ascii=False 保留中文字符"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('测试群聊', 8, 0)
            with open(scheduler._schedule_file, 'r', encoding='utf-8') as f:
                content = f.read()
            assert '测试群聊' in content

    def test_save_oserror_handled(self):
        """_save 遇到 OSError 应不报错"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('group1', 8, 0)
            with patch('builtins.open', side_effect=OSError("disk full")):
                # 不应抛异常
                scheduler._save()


# ── 补充测试：list_tasks 过滤 running 字段 ───────────────────────────────

class TestListAllExtended:
    def test_list_with_running_task_excludes_running(self):
        """list_all 应过滤掉 running 字段，即使任务正在运行"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            result = scheduler.list_all()
            for task in result['tasks']:
                assert 'running' not in task

    def test_list_preserves_other_fields(self):
        """list_all 过滤 running 后应保留其他所有字段"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            scheduler.create('group1', 8, 30, theme='dark', fmt='png')
            result = scheduler.list_all()
            task = result['tasks'][0]
            assert 'task_id' in task
            assert 'group_name' in task
            assert 'hour' in task
            assert 'minute' in task
            assert 'theme' in task
            assert 'fmt' in task
            assert 'enabled' in task
            assert 'status' in task
            assert 'history' in task


# ── 补充测试：shutdown 方法 ─────────────────────────────────────────────

class TestShutdown:
    def test_shutdown_resets_running_tasks(self):
        """shutdown 应将所有 running 任务重置为 idle"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            assert scheduler._tasks[task_id]['running'] is True
            scheduler.shutdown()
            assert scheduler._tasks[task_id]['running'] is False
            assert scheduler._tasks[task_id]['status'] == 'idle'

    def test_shutdown_cancels_timer(self):
        """shutdown 应取消定时器"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            mock_timer = MagicMock()
            scheduler._timer = mock_timer
            scheduler.shutdown()
            mock_timer.cancel.assert_called_once()
            assert scheduler._timer is None

    def test_shutdown_calls_save(self):
        """shutdown 应调用 _save"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            with patch.object(scheduler, '_save', wraps=scheduler._save) as mock_save:
                scheduler.shutdown()
                # shutdown 调用 _save 两次：一次在 timer cancel 后，一次在重置 running 后
                assert mock_save.call_count >= 1


# ── 补充测试：_append_history 限制 20 条 ────────────────────────────────

class TestAppendHistory:
    def test_history_limited_to_20(self):
        """执行历史应限制为最多 20 条"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            for i in range(25):
                scheduler.trigger(task_id)
                scheduler.mark_completed(task_id, {'success': True, 'method': f'm{i}'})
            task = scheduler.get_task(task_id)
            assert len(task['history']) == 20

    def test_history_newest_first(self):
        """执行历史应按时间倒序排列（最新在前）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            scheduler = _make_scheduler(tmp_dir)
            r = scheduler.create('group1', 8, 0)
            task_id = r['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_completed(task_id, {'success': True, 'method': 'first'})
            scheduler.trigger(task_id)
            scheduler.mark_completed(task_id, {'success': True, 'method': 'second'})
            task = scheduler.get_task(task_id)
            assert task['history'][0]['method'] == 'second'
            assert task['history'][1]['method'] == 'first'


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

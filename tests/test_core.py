import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from chatlens.core.models import ChatMessage
from chatlens.core import GroupAnalysis
from chatlens.core.analyzer import GroupStatsAnalyzer
from chatlens.core.ai_analyzer import rule_based_analysis
from chatlens.core.chatlog_bridge import ChatlogBridge, _try_zstd_decompress
from chatlens.plugins.schedule.scheduler import TaskScheduler
from chatlens.plugins.web.ide_tasks import IDETaskQueue


def _make_messages(n=20):
    msgs = []
    for i in range(n):
        msgs.append(ChatMessage(
            sender=f'用户{i % 5}',
            content=f'这是第{i}条消息，内容关于AI和编程',
            msg_type='text',
            msg_attr='',
            timestamp=f'2026-05-{(i % 28) + 1:02d} 10:{i % 60:02d}:00',
            group_name='测试群',
            sender_remark=f'备注{i % 5}',
        ))
    return msgs


class TestChatMessage:
    def test_to_dict(self):
        msg = ChatMessage(sender='Alice', content='Hello', msg_type='text', msg_attr='', timestamp='2026-01-01 12:00:00', group_name='TestGroup')
        d = msg.to_dict()
        assert d['sender'] == 'Alice'
        assert d['content'] == 'Hello'
        assert d['group_name'] == 'TestGroup'

    def test_default_fields(self):
        msg = ChatMessage(sender='Bob', content='Hi', msg_type='text', msg_attr='', timestamp='2026-01-01', group_name='G')
        assert msg.sender_remark == ""
        assert msg.quote_content == ""


class TestGroupAnalysisCollector:
    def test_init_creates_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, 'test_data')
            ga = GroupAnalysis({'data_dir': data_dir})
            assert os.path.exists(data_dir)

    def test_set_and_get_messages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            msgs = _make_messages(5)
            ga.set_messages('test_group', msgs)
            assert ga.get_messages('test_group') == msgs

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            msgs = _make_messages(3)
            ga.set_messages('test_group', msgs)
            ga.save_loaded('test_group', msgs)
            ga.collector_data.pop('test_group', None)
            loaded = ga.load_from_file('test_group')
            assert len(loaded) == 3
            assert loaded[0].sender == msgs[0].sender

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            loaded = ga.load_from_file('nonexistent')
            assert loaded == []

    def test_get_all_groups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            ga.set_messages('g1', _make_messages(1))
            ga.set_messages('g2', _make_messages(1))
            groups = ga.get_groups()
            assert 'g1' in groups
            assert 'g2' in groups

    def test_delete_messages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            ga.set_messages('del_group', _make_messages(2))
            ga.save_loaded('del_group', _make_messages(2))
            result = ga.delete_loaded('del_group')
            assert result is True
            assert ga.get_messages('del_group') == []

    def test_delete_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            result = ga.delete_loaded('no_such_group')
            assert result is False


class TestGroupStatsAnalyzer:
    def test_analyze_empty(self):
        analyzer = GroupStatsAnalyzer()
        result = analyzer.analyze([])
        assert result['overview']['total_messages'] == 0
        assert result['member_stats'] == []

    def test_analyze_basic(self):
        analyzer = GroupStatsAnalyzer()
        msgs = _make_messages(20)
        result = analyzer.analyze(msgs)
        assert result['overview']['total_messages'] == 20
        assert len(result['member_stats']) > 0
        assert result['overview']['total_members'] > 0

    def test_analyze_has_keyword_cloud(self):
        analyzer = GroupStatsAnalyzer()
        msgs = _make_messages(30)
        result = analyzer.analyze(msgs)
        assert 'keyword_cloud' in result
        assert isinstance(result['keyword_cloud'], list)

    def test_analyze_hourly_distribution(self):
        analyzer = GroupStatsAnalyzer()
        msgs = _make_messages(10)
        result = analyzer.analyze(msgs)
        assert 'hourly_distribution' in result
        assert isinstance(result['hourly_distribution'], list)

    def test_analyze_interaction(self):
        analyzer = GroupStatsAnalyzer()
        msgs = _make_messages(15)
        result = analyzer.analyze(msgs)
        assert 'interaction_analysis' in result
        assert 'top_interactions' in result['interaction_analysis']


class TestRuleBasedAnalysis:
    def test_empty_messages(self):
        result = rule_based_analysis([])
        assert 'summary' in result
        assert 'user_titles' in result
        assert 'golden_quotes' in result
        assert 'chat_quality' in result

    def test_basic_analysis(self):
        msgs = _make_messages(30)
        result = rule_based_analysis(msgs)
        assert 'summary' in result
        assert 'user_titles' in result
        assert 'golden_quotes' in result
        assert 'chat_quality' in result
        assert 'keywords' in result

    def test_summary_has_content(self):
        msgs = _make_messages(20)
        result = rule_based_analysis(msgs)
        summary = result.get('summary', {})
        assert 'summary' in summary or 'highlights' in summary or 'topics' in summary

    def test_user_titles_structure(self):
        msgs = _make_messages(25)
        result = rule_based_analysis(msgs)
        titles = result.get('user_titles', {})
        if 'user_titles' in titles and titles['user_titles']:
            first = titles['user_titles'][0]
            assert 'name' in first or '昵称' in first or 'title' in first or '称号' in first


class TestZstdDecompress:
    def test_non_zstd_data(self):
        result = _try_zstd_decompress(b'hello world')
        assert result == 'hello world'

    def test_zstd_magic_not_decompressed_without_lib(self):
        data = b'\x28\xb5\x2f\xfd' + b'\x00' * 20
        try:
            result = _try_zstd_decompress(data)
            assert isinstance(result, str)
        except Exception:
            pass


class TestChatlogBridge:
    def test_init_no_db(self):
        bridge = ChatlogBridge(api_base='http://localhost:5030', db_path=None)
        assert bridge._contact_db is None or bridge._msg_db is not None

    def test_is_available_no_db(self):
        bridge = ChatlogBridge(api_base='http://localhost:5030', db_path=None)
        assert isinstance(bridge.is_available(), bool)

    def test_get_messages_no_db(self):
        bridge = ChatlogBridge(api_base='http://localhost:5030', db_path=None)
        result = bridge.get_messages('test@chatroom')
        assert isinstance(result, list)


class TestTaskScheduler:
    def test_create_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            result = scheduler.create('test_group', 9, 0)
            assert result['success'] is True
            assert 'task_id' in result
            scheduler.shutdown()

    def test_create_task_invalid_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            result = scheduler.create('test_group', 25, 0)
            assert result['success'] is False
            scheduler.shutdown()

    def test_create_task_no_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            result = scheduler.create('', 9, 0)
            assert result['success'] is False
            scheduler.shutdown()

    def test_list_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            scheduler.create('g1', 9, 0)
            scheduler.create('g2', 10, 30)
            result = scheduler.list_all()
            assert result['success'] is True
            assert len(result['tasks']) == 2
            scheduler.shutdown()

    def test_delete_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            created = scheduler.create('g1', 9, 0)
            task_id = created['task_id']
            result = scheduler.delete(task_id)
            assert result['success'] is True
            result2 = scheduler.delete('nonexistent')
            assert result2['success'] is False
            scheduler.shutdown()

    def test_toggle_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            created = scheduler.create('g1', 9, 0)
            task_id = created['task_id']
            result = scheduler.toggle(task_id, False)
            assert result['success'] is True
            scheduler.shutdown()

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler1 = TaskScheduler(sched_file)
            scheduler1.create('persist_group', 8, 30)
            scheduler1.shutdown()
            scheduler2 = TaskScheduler(sched_file)
            result = scheduler2.list_all()
            assert len(result['tasks']) == 1
            assert result['tasks'][0]['group_name'] == 'persist_group'
            scheduler2.shutdown()

    def test_mark_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            created = scheduler.create('g1', 9, 0)
            task_id = created['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_completed(task_id, {'success': True, 'method': 'rules'})
            task = scheduler.get_task(task_id)
            assert task['status'] == 'completed'
            scheduler.shutdown()

    def test_mark_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            created = scheduler.create('g1', 9, 0)
            task_id = created['task_id']
            scheduler.trigger(task_id)
            scheduler.mark_failed(task_id, 'test error')
            task = scheduler.get_task(task_id)
            assert task['status'] == 'failed'
            scheduler.shutdown()

    def test_history_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sched_file = os.path.join(tmpdir, 'tasks.json')
            scheduler = TaskScheduler(sched_file)
            created = scheduler.create('g1', 9, 0)
            task_id = created['task_id']
            for i in range(25):
                scheduler.trigger(task_id)
                scheduler.mark_completed(task_id, {'success': True, 'method': 'rules'})
            task = scheduler.get_task(task_id)
            assert len(task.get('history', [])) <= 20
            scheduler.shutdown()


class TestIDETaskQueue:
    def test_create_task(self):
        queue = IDETaskQueue()
        result = queue.create('test_group')
        assert result['success'] is True
        assert 'task_id' in result

    def test_get_task(self):
        queue = IDETaskQueue()
        created = queue.create('test_group')
        task_id = created['task_id']
        result = queue.get(task_id)
        assert result['success'] is True
        assert result['task']['group_name'] == 'test_group'

    def test_get_nonexistent(self):
        queue = IDETaskQueue()
        result = queue.get('no_such_id')
        assert result['success'] is False

    def test_submit_result(self):
        queue = IDETaskQueue()
        created = queue.create('test_group')
        task_id = created['task_id']
        result = queue.submit_result(task_id, {'summary': 'test'})
        assert result['success'] is True
        task = queue.get(task_id)
        assert task['task']['status'] == 'completed'

    def test_mark_failed(self):
        queue = IDETaskQueue()
        created = queue.create('test_group')
        task_id = created['task_id']
        queue.mark_failed(task_id, 'error msg')
        task = queue.get(task_id)
        assert task['task']['status'] == 'failed'

    def test_mark_completed(self):
        queue = IDETaskQueue()
        created = queue.create('test_group')
        task_id = created['task_id']
        queue.mark_completed(task_id, {'summary': 'done'}, {'html_url': '/test'})
        task = queue.get(task_id)
        assert task['task']['status'] == 'completed'
        assert task['task']['report'] == {'html_url': '/test'}


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])

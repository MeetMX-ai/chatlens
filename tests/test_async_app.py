"""async_app.py 单元测试 — 覆盖所有主要 API 端点"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient


# ── 辅助：构造 mock ga ──────────────────────────────────────

def _make_mock_ga():
    ga = MagicMock()
    ga.config = {'ai_service': {'api_key': 'sk-test'}}
    ga.get_reports_dir.return_value = '/tmp/reports'
    ga.report_templates_dir = '/tmp/templates'
    ga.get_provider.return_value = None
    return ga


def _make_mock_web():
    web = MagicMock()
    web.get_health.return_value = {'status': 'ok', 'uptime': 100}
    web.get_config.return_value = {'success': True, 'config': {'key': 'val'}}
    web.save_config.return_value = {'success': True, 'message': '已保存'}
    web.get_groups.return_value = {'groups': ['group1', 'group2']}
    web.load_from_chatlog.return_value = {'success': True, 'count': 10}
    web.get_stats.return_value = {'success': True, 'data': {'total': 100}}
    web.get_ai_analysis.return_value = {'success': True, 'data': {}}
    web.create_scheduled_task.return_value = {'success': True, 'task_id': 't1'}
    web.list_scheduled_tasks.return_value = {'success': True, 'tasks': []}
    web.delete_data_batch.return_value = {'success': True, 'deleted': 0, 'failed': 0}
    return web


def _make_mock_report():
    report = MagicMock()
    report.list_reports.return_value = {'success': True, 'reports': []}
    report.delete_report.return_value = {'success': True, 'message': '已删除'}
    return report


def _create_client(ga=None, web=None, report=None):
    """创建 TestClient，注入 mock ga/web/report"""
    if ga is None:
        ga = _make_mock_ga()
    if web is None:
        web = _make_mock_web()
    if report is None:
        report = _make_mock_report()
    ga.web = web
    ga.report = report
    from chatlens.plugins.web.async_app import create_app
    app = create_app(ga=ga)
    return TestClient(app)


# ═══════════════════════════════════════════════════════════
#  1. GET /api/health
# ═══════════════════════════════════════════════════════════

class TestHealth:
    def test_health_ok(self):
        client = _create_client()
        resp = client.get('/api/health')
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'ok'

    def test_health_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/health')
        assert resp.status_code == 200
        assert resp.json()['status'] == 'error'


# ═══════════════════════════════════════════════════════════
#  2. GET /api/config
# ═══════════════════════════════════════════════════════════

class TestGetConfig:
    def test_get_config_ok(self):
        client = _create_client()
        resp = client.get('/api/config')
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_get_config_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/config')
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  3. POST /api/config/save
# ═══════════════════════════════════════════════════════════

class TestSaveConfig:
    def test_save_config_ok(self):
        client = _create_client()
        resp = client.post('/api/config/save', json={'key': 'val'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_save_config_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/config/save', json={'key': 'val'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  4. GET /api/groups
# ═══════════════════════════════════════════════════════════

class TestGetGroups:
    def test_get_groups_ok(self):
        client = _create_client()
        resp = client.get('/api/groups')
        assert resp.status_code == 200
        assert 'groups' in resp.json()

    def test_get_groups_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/groups')
        assert resp.json()['groups'] == []


# ═══════════════════════════════════════════════════════════
#  5. POST /api/chatlog/load
# ═══════════════════════════════════════════════════════════

class TestLoadData:
    def test_load_ok(self):
        client = _create_client()
        resp = client.post('/api/chatlog/load', json={'talker': 'group1', 'limit': 100})
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_load_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/chatlog/load', json={'talker': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  6. GET /api/analysis/stats
# ═══════════════════════════════════════════════════════════

class TestGetStats:
    def test_stats_ok(self):
        client = _create_client()
        resp = client.get('/api/analysis/stats', params={'group': 'group1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_stats_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/analysis/stats', params={'group': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  7. POST /api/analysis/ai
# ═══════════════════════════════════════════════════════════

class TestAIAnalysis:
    def test_ai_analysis_ok(self):
        client = _create_client()
        resp = client.post('/api/analysis/ai', json={'group_name': 'group1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_ai_analysis_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/analysis/ai', json={'group_name': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  8. GET /api/reports
# ═══════════════════════════════════════════════════════════

class TestGetReports:
    def test_reports_ok(self):
        client = _create_client()
        resp = client.get('/api/reports')
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_reports_no_report_service(self):
        ga = _make_mock_ga()
        ga.web = _make_mock_web()
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/reports')
        assert resp.json()['success'] is True
        assert resp.json()['reports'] == []


# ═══════════════════════════════════════════════════════════
#  9. GET /api/reports/download — 路径穿越测试
# ═══════════════════════════════════════════════════════════

class TestDownloadReport:
    def test_download_missing_file_param(self):
        client = _create_client()
        resp = client.get('/api/reports/download', params={'file': ''})
        assert resp.json()['success'] is False

    def test_download_path_traversal_rejected(self):
        client = _create_client()
        resp = client.get('/api/reports/download', params={'file': '../../../etc/passwd'})
        data = resp.json()
        assert data['success'] is False
        # 非法文件名或非法路径
        assert data.get('error') in ('非法文件名', '非法路径')

    def test_download_nonexistent_file(self, tmp_path):
        ga = _make_mock_ga()
        ga.get_reports_dir.return_value = str(tmp_path)
        ga.web = _make_mock_web()
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/reports/download', params={'file': 'nonexistent.html'})
        assert resp.json()['success'] is False
        assert '不存在' in resp.json()['error']

    def test_download_valid_file(self, tmp_path):
        ga = _make_mock_ga()
        report_file = tmp_path / "test_report.html"
        report_file.write_text("<html>test</html>", encoding='utf-8')
        ga.get_reports_dir.return_value = str(tmp_path)
        ga.web = _make_mock_web()
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/reports/download', params={'file': 'test_report.html'})
        assert resp.status_code == 200

    def test_download_filename_with_middot_and_fullwidth_paren_accepted(self, tmp_path):
        """含中点 · 和全角圆括号 （） 的合法群名应被接受"""
        ga = _make_mock_ga()
        report_file = tmp_path / "HR·AI黑客松（2群）.html"
        report_file.write_text("<html>test</html>", encoding='utf-8')
        ga.get_reports_dir.return_value = str(tmp_path)
        ga.web = _make_mock_web()
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/reports/download', params={'file': 'HR·AI黑客松（2群）.html'})
        # 走到这一步说明 regex 校验通过；文件存在时 status 200，不存在时 success=False 带"文件不存在"
        if resp.status_code == 200:
            assert b'<html>test</html>' in resp.content
        else:
            data = resp.json()
            assert data.get('success') is False
            assert '不存在' in data.get('error', '')

    def test_download_path_separators_still_rejected(self):
        """含 / 或 \\ 的文件名仍应被 regex 拒掉"""
        client = _create_client()
        for bad in ('subdir/file.html', 'subdir\\file.html', '..\\..\\etc\\passwd', '../etc/passwd'):
            resp = client.get('/api/reports/download', params={'file': bad})
            data = resp.json()
            assert data['success'] is False
            assert data.get('error') in ('非法文件名', '非法路径')


# ═══════════════════════════════════════════════════════════
#  10. POST /api/schedule/create
# ═══════════════════════════════════════════════════════════

class TestScheduleCreate:
    def test_create_ok(self):
        client = _create_client()
        resp = client.post('/api/schedule/create', json={
            'group_name': 'group1', 'hour': 9, 'minute': 0
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_create_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/schedule/create', json={'group_name': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  11. GET /api/schedule/list
# ═══════════════════════════════════════════════════════════

class TestScheduleList:
    def test_list_ok(self):
        client = _create_client()
        resp = client.get('/api/schedule/list')
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_list_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/schedule/list')
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  12. POST /api/analysis/compare — 群对比分析
# ═══════════════════════════════════════════════════════════

class TestCompareGroups:
    def test_compare_ok(self):
        web = _make_mock_web()
        web.compare_groups.return_value = {'success': True, 'data': {'comparison': []}}
        client = _create_client(web=web)
        resp = client.post('/api/analysis/compare', json={'groups': ['g1', 'g2']})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.compare_groups.assert_called_once_with(['g1', 'g2'])

    def test_compare_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/analysis/compare', json={'groups': ['g1']})
        assert resp.json()['success'] is False
        assert resp.json().get('error') == '服务未就绪'


# ═══════════════════════════════════════════════════════════
#  13. GET /api/analysis/daily — 每日统计
# ═══════════════════════════════════════════════════════════

class TestDailyAnalysis:
    def test_daily_ok(self):
        web = _make_mock_web()
        web.get_daily_analysis.return_value = {'success': True, 'data': {}}
        client = _create_client(web=web)
        resp = client.get('/api/analysis/daily', params={'group': 'g1', 'date': '2025-01-01'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_daily_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/analysis/daily', params={'group': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  14. GET /api/data/export — 数据导出
# ═══════════════════════════════════════════════════════════

class TestExportData:
    def test_export_streaming(self):
        web = _make_mock_web()
        web.export_data.return_value = (b'data,content', 'export.csv', 'text/csv')
        client = _create_client(web=web)
        resp = client.get('/api/data/export', params={'group': 'g1', 'fmt': 'csv'})
        assert resp.status_code == 200
        assert 'text/csv' in resp.headers.get('content-type', '')
        assert b'data,content' in resp.content

    def test_export_json_response(self):
        web = _make_mock_web()
        web.export_data.return_value = {'success': False, 'error': '无数据'}
        client = _create_client(web=web)
        resp = client.get('/api/data/export', params={'group': 'g1', 'fmt': 'csv'})
        assert resp.json()['success'] is False

    def test_export_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/data/export', params={'group': 'g1'})
        assert resp.status_code == 503


# ═══════════════════════════════════════════════════════════
#  15. DELETE /api/schedule/delete — 删除定时任务
# ═══════════════════════════════════════════════════════════

class TestScheduleDelete:
    def test_delete_ok(self):
        web = _make_mock_web()
        web.delete_scheduled_task.return_value = {'success': True}
        client = _create_client(web=web)
        resp = client.request('DELETE', '/api/schedule/delete', json={'task_id': 't1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.delete_scheduled_task.assert_called_once_with('t1')

    def test_delete_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.request('DELETE', '/api/schedule/delete', json={'task_id': 't1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  16. POST /api/schedule/toggle — 启用/禁用定时任务
# ═══════════════════════════════════════════════════════════

class TestScheduleToggle:
    def test_toggle_ok(self):
        web = _make_mock_web()
        web.toggle_scheduled_task.return_value = {'success': True, 'enabled': False}
        client = _create_client(web=web)
        resp = client.post('/api/schedule/toggle', json={'task_id': 't1', 'enabled': False})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.toggle_scheduled_task.assert_called_once_with('t1', enabled=False)

    def test_toggle_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/schedule/toggle', json={'task_id': 't1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  17. POST /api/schedule/trigger — 手动触发定时任务
# ═══════════════════════════════════════════════════════════

class TestScheduleTrigger:
    def test_trigger_ok(self):
        web = _make_mock_web()
        web.trigger_scheduled_task.return_value = {'success': True}
        client = _create_client(web=web)
        resp = client.post('/api/schedule/trigger', json={'task_id': 't1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.trigger_scheduled_task.assert_called_once_with('t1')

    def test_trigger_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/schedule/trigger', json={'task_id': 't1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  18. GET /api/ide/task — IDE 任务查询
# ═══════════════════════════════════════════════════════════

class TestIdeTask:
    def test_ide_task_ok(self):
        web = _make_mock_web()
        web.get_ide_task.return_value = {'success': True, 'task': {'id': 't1'}}
        client = _create_client(web=web)
        resp = client.get('/api/ide/task', params={'task_id': 't1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_ide_task_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/ide/task', params={'task_id': 't1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  19. POST /api/ide/task/result — IDE 提交分析结果
# ═══════════════════════════════════════════════════════════

class TestIdeSubmitResult:
    def test_submit_ok(self):
        web = _make_mock_web()
        web.submit_ide_result.return_value = {'success': True}
        client = _create_client(web=web)
        resp = client.post('/api/ide/task/result', json={'task_id': 't1', 'result': {'summary': 'test'}})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.submit_ide_result.assert_called_once_with('t1', {'summary': 'test'})

    def test_submit_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/ide/task/result', json={'task_id': 't1', 'result': {}})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  20. POST /api/analysis/ide-prompt — IDE 提示词
# ═══════════════════════════════════════════════════════════

class TestIdePrompt:
    def test_prompt_ok(self):
        web = _make_mock_web()
        web.get_ide_prompt.return_value = {'success': True, 'prompt': '分析群聊...'}
        client = _create_client(web=web)
        resp = client.post('/api/analysis/ide-prompt', json={'group_name': 'g1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_prompt_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/analysis/ide-prompt', json={'group_name': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  21. GET /api/status — 服务状态
# ═══════════════════════════════════════════════════════════

class TestStatus:
    def test_status_ok(self):
        web = _make_mock_web()
        web.get_status.return_value = {
            'api_key_configured': True,
            'ollama_available': False,
            'ide_available': False,
            'error_count': 0,
        }
        client = _create_client(web=web)
        resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.json()
        # H2 修复：/api/status 只返 4 个轻量字段
        assert data['api_key_configured'] is True
        assert data['ollama_available'] is False

    def test_status_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/status')
        data = resp.json()
        # H2 修复：web 未初始化时返回新的 4 个轻量字段默认值
        assert data['api_key_configured'] is False
        assert data['ollama_available'] is False
        assert data['ide_available'] is False
        assert data['error_count'] == 0


# ═══════════════════════════════════════════════════════════
#  21b. GET /api/status/details — 服务状态详情（H2 修复后新路由）
# ═══════════════════════════════════════════════════════════

class TestStatusDetails:
    def test_status_details_ok(self):
        web = _make_mock_web()
        web._compute_status_details.return_value = {
            'chatlog_available': True,
            'chatlog_talkers_count': 5,
            'groups': ['g1'],
            'report_count': 2,
            'report_total_size_kb': 4.0,
        }
        client = _create_client(web=web)
        resp = client.get('/api/status/details')
        assert resp.status_code == 200
        data = resp.json()
        assert data['chatlog_available'] is True
        assert data['chatlog_talkers_count'] == 5
        assert data['groups'] == ['g1']
        assert data['report_count'] == 2

    def test_status_details_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/status/details')
        data = resp.json()
        assert data['chatlog_available'] is False
        assert data['chatlog_talkers_count'] == 0
        assert data['groups'] == []
        assert data['report_count'] == 0


# ═══════════════════════════════════════════════════════════
#  22. POST /api/analysis/daily/auto-report — 每日自动报告
# ═══════════════════════════════════════════════════════════

class TestDailyAutoReport:
    def test_daily_report_ok(self):
        web = _make_mock_web()
        web.daily_auto_report.return_value = {'success': True, 'report': {}}
        client = _create_client(web=web)
        resp = client.post('/api/analysis/daily/auto-report', json={
            'group_name': 'g1', 'date': '2025-01-01', 'theme': 'scrapbook', 'fmt': 'jpg'
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.daily_auto_report.assert_called_once_with('g1', '2025-01-01', theme='scrapbook', fmt='jpg')

    def test_daily_report_no_web(self):
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/analysis/daily/auto-report', json={'group_name': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  23. GET /api/reports/download — 下载报告文件（补充测试）
# ═══════════════════════════════════════════════════════════

class TestDownloadReportExtended:
    def test_download_pdf_file(self, tmp_path):
        ga = _make_mock_ga()
        report_file = tmp_path / "report_1718000000.pdf"
        report_file.write_bytes(b'%PDF-1.4 test')
        ga.get_reports_dir.return_value = str(tmp_path)
        ga.web = _make_mock_web()
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/reports/download', params={'file': 'report_1718000000.pdf'})
        assert resp.status_code == 200
        assert 'application/pdf' in resp.headers.get('content-type', '')

    def test_download_jpg_file(self, tmp_path):
        ga = _make_mock_ga()
        report_file = tmp_path / "report_1718000000.jpg"
        report_file.write_bytes(b'\xff\xd8\xff\xe0')
        ga.get_reports_dir.return_value = str(tmp_path)
        ga.web = _make_mock_web()
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/reports/download', params={'file': 'report_1718000000.jpg'})
        assert resp.status_code == 200
        assert 'image/jpeg' in resp.headers.get('content-type', '')

    def test_download_path_traversal_symlink_rejected(self, tmp_path):
        """路径穿越：文件名合法但路径指向外部目录"""
        ga = _make_mock_ga()
        ga.get_reports_dir.return_value = str(tmp_path)
        ga.web = _make_mock_web()
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        # 使用 URL 编码的路径穿越
        resp = client.get('/api/reports/download', params={'file': '..%2F..%2Fetc%2Fpasswd'})
        data = resp.json()
        assert data['success'] is False

    def test_download_content_disposition_header(self, tmp_path):
        """验证 Content-Disposition 头包含 UTF-8 文件名"""
        ga = _make_mock_ga()
        report_file = tmp_path / "测试报告.html"
        report_file.write_text("<html>test</html>", encoding='utf-8')
        ga.get_reports_dir.return_value = str(tmp_path)
        ga.web = _make_mock_web()
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/reports/download', params={'file': '测试报告.html'})
        assert resp.status_code == 200
        cd = resp.headers.get('content-disposition', '')
        assert 'UTF-8' in cd


# ═══════════════════════════════════════════════════════════
#  24. 静态文件服务
# ═══════════════════════════════════════════════════════════

class TestStaticFiles:
    def test_static_index_accessible(self):
        """测试静态文件 index.html 可访问"""
        client = _create_client()
        resp = client.get('/index.html')
        # 如果 web 目录存在，应返回 200；否则可能 404
        assert resp.status_code in (200, 404)


# ═══════════════════════════════════════════════════════════
#  25. CORS 中间件
# ═══════════════════════════════════════════════════════════

class TestCORS:
    def test_cors_headers_on_get(self):
        """带 Origin 头的 GET 请求应包含 CORS 响应头（默认允许 http://localhost:8080）"""
        client = _create_client()
        resp = client.get('/api/health', headers={'Origin': 'http://localhost:8080'})
        assert resp.headers.get('access-control-allow-origin') == 'http://localhost:8080'

    def test_cors_preflight_options(self):
        """OPTIONS 预检请求应返回 CORS 头（默认允许 GET/POST/DELETE/OPTIONS）"""
        client = _create_client()
        resp = client.options('/api/health', headers={
            'Origin': 'http://localhost:8080',
            'Access-Control-Request-Method': 'GET',
        })
        assert resp.headers.get('access-control-allow-origin') == 'http://localhost:8080'
        assert 'access-control-allow-methods' in resp.headers


# ═══════════════════════════════════════════════════════════
#  26. POST /api/analysis/ai — AI 分析扩展测试（use_rules 参数）
# ═══════════════════════════════════════════════════════════

class TestAIAnalysisExtended:
    def test_ai_analysis_with_use_rules_true(self):
        """AI 分析传入 use_rules=True 应正确传递参数"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/analysis/ai', json={'group_name': 'g1', 'use_rules': True})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        # H1 修复：路由多传 use_ide 默认值
        web.get_ai_analysis.assert_called_once_with('g1', use_rules=True, use_ide=False)

    def test_ai_analysis_with_use_rules_false(self):
        """AI 分析传入 use_rules=False 应正确传递参数"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/analysis/ai', json={'group_name': 'g1', 'use_rules': False})
        assert resp.status_code == 200
        web.get_ai_analysis.assert_called_once_with('g1', use_rules=False, use_ide=False)

    def test_ai_analysis_default_use_rules(self):
        """AI 分析不传 use_rules 应默认为 False"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/analysis/ai', json={'group_name': 'g1'})
        assert resp.status_code == 200
        web.get_ai_analysis.assert_called_once_with('g1', use_rules=False, use_ide=False)

    def test_ai_analysis_empty_group_name(self):
        """AI 分析不传 group_name 应默认为空字符串"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/analysis/ai', json={})
        assert resp.status_code == 200
        web.get_ai_analysis.assert_called_once_with('', use_rules=False, use_ide=False)

    def test_ai_analysis_passes_use_ide(self):
        """H1 修复：AI 分析可显式传 use_ide"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/analysis/ai', json={'group_name': 'g1', 'use_ide': True})
        assert resp.status_code == 200
        web.get_ai_analysis.assert_called_once_with('g1', use_rules=False, use_ide=True)


# ═══════════════════════════════════════════════════════════
#  27. GET /api/analysis/stats — 带群名的统计查询扩展
# ═══════════════════════════════════════════════════════════

class TestGetStatsExtended:
    def test_stats_with_group_name(self):
        """统计查询应正确传递群名参数"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.get('/api/analysis/stats', params={'group': '测试群'})
        assert resp.status_code == 200
        web.get_stats.assert_called_once_with('测试群')

    def test_stats_empty_group(self):
        """统计查询不传群名应默认为空字符串"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.get('/api/analysis/stats')
        assert resp.status_code == 200
        web.get_stats.assert_called_once_with('')


# ═══════════════════════════════════════════════════════════
#  28. POST /api/chatlog/load — 加载失败情况
# ═══════════════════════════════════════════════════════════

class TestLoadDataFailure:
    def test_load_failure_response(self):
        """加载失败时应返回 success=False"""
        web = _make_mock_web()
        web.load_from_chatlog.return_value = {'success': False, 'error': '数据库连接失败'}
        client = _create_client(web=web)
        resp = client.post('/api/chatlog/load', json={'talker': 'group1', 'limit': 100})
        assert resp.status_code == 200
        assert resp.json()['success'] is False
        assert '数据库连接失败' in resp.json()['error']

    def test_load_passes_talker_and_limit(self):
        """加载应正确传递 talker 和 limit 参数"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/chatlog/load', json={'talker': 'g1', 'limit': 50})
        assert resp.status_code == 200
        web.load_from_chatlog.assert_called_once_with('g1', 50)

    def test_load_default_limit(self):
        """不传 limit 应默认为 0"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/chatlog/load', json={'talker': 'g1'})
        assert resp.status_code == 200
        web.load_from_chatlog.assert_called_once_with('g1', 0)


# ═══════════════════════════════════════════════════════════
#  29. POST /api/analysis/ide-prompt — IDE 提示词获取
# ═══════════════════════════════════════════════════════════

class TestIdePromptExtended:
    def test_prompt_passes_group_name(self):
        """IDE 提示词应正确传递群名参数"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/analysis/ide-prompt', json={'group_name': '测试群'})
        assert resp.status_code == 200
        web.get_ide_prompt.assert_called_once_with('测试群')

    def test_prompt_default_group_name(self):
        """不传群名应默认为空字符串"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/analysis/ide-prompt', json={})
        assert resp.status_code == 200
        web.get_ide_prompt.assert_called_once_with('')


# ═══════════════════════════════════════════════════════════
#  30. POST /api/ide/task/result — IDE 提交分析结果扩展
# ═══════════════════════════════════════════════════════════

class TestIdeSubmitResultExtended:
    def test_submit_passes_task_id_and_result(self):
        """IDE 提交结果应正确传递 task_id 和 result"""
        web = _make_mock_web()
        client = _create_client(web=web)
        result_data = {'summary': '测试分析', 'score': 85}
        resp = client.post('/api/ide/task/result', json={'task_id': 't1', 'result': result_data})
        assert resp.status_code == 200
        web.submit_ide_result.assert_called_once_with('t1', result_data)

    def test_submit_default_values(self):
        """不传参数应使用默认值"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.post('/api/ide/task/result', json={})
        assert resp.status_code == 200
        web.submit_ide_result.assert_called_once_with('', {})


# ═══════════════════════════════════════════════════════════
#  31. GET /api/ide/task — IDE 任务查询扩展
# ═══════════════════════════════════════════════════════════

class TestIdeTaskExtended:
    def test_ide_task_passes_task_id(self):
        """IDE 任务查询应正确传递 task_id"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.get('/api/ide/task', params={'task_id': 'abc123'})
        assert resp.status_code == 200
        web.get_ide_task.assert_called_once_with('abc123')

    def test_ide_task_default_task_id(self):
        """不传 task_id 应默认为空字符串"""
        web = _make_mock_web()
        client = _create_client(web=web)
        resp = client.get('/api/ide/task')
        assert resp.status_code == 200
        web.get_ide_task.assert_called_once_with('')


# ═══════════════════════════════════════════════════════════
#  32. 错误处理 — 服务未初始化时的错误响应
# ═══════════════════════════════════════════════════════════

class TestServiceNotInitialized:
    def test_health_error_response(self):
        """服务未初始化时 health 应返回 error 状态"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/health')
        assert resp.json()['status'] == 'error'
        assert '未初始化' in resp.json()['message']

    def test_status_default_response(self):
        """服务未初始化时 status 应返回默认值（H2 修复后）"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/status')
        data = resp.json()
        # H2 修复：web 未初始化时返回 4 个轻量字段默认值
        assert data['api_key_configured'] is False
        assert data['ollama_available'] is False
        assert data['ide_available'] is False
        assert data['error_count'] == 0

    def test_groups_empty_response(self):
        """服务未初始化时 groups 应返回空列表"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/groups')
        assert resp.json()['groups'] == []

    def test_docs_empty_response(self):
        """服务未初始化时 docs 应返回空端点列表"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/docs')
        assert resp.json()['success'] is True
        assert resp.json()['endpoints'] == []

    def test_data_files_empty_response(self):
        """服务未初始化时 data-files 应返回空列表"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/data-files')
        assert resp.json()['files'] == []

    def test_chatrooms_empty_response(self):
        """服务未初始化时 chatrooms 应返回空列表"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/chatlog/chatrooms')
        assert resp.json()['chatrooms'] == []

    def test_talkers_empty_response(self):
        """服务未初始化时 talkers 应返回空列表"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/chatlog/talkers')
        assert resp.json()['talkers'] == []

    def test_refresh_error_response(self):
        """服务未初始化时 refresh 应返回错误"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/chatlog/refresh')
        assert resp.json()['success'] is False
        assert '未初始化' in resp.json()['error']


# ═══════════════════════════════════════════════════════════
#  33. create_app() — 应用创建与中间件配置
# ═══════════════════════════════════════════════════════════

class TestCreateApp:
    def test_create_app_returns_fastapi_instance(self):
        """create_app 应返回 FastAPI 实例"""
        from chatlens.plugins.web.async_app import create_app
        from fastapi import FastAPI
        app = create_app(ga=_make_mock_ga())
        assert isinstance(app, FastAPI)

    def test_create_app_stores_ga_in_state(self):
        """create_app 应将 ga 存储到 app.state"""
        from chatlens.plugins.web.async_app import create_app
        ga = _make_mock_ga()
        app = create_app(ga=ga)
        assert app.state.ga is ga

    def test_create_app_with_none_ga(self):
        """create_app 传入 ga=None 应不报错"""
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=None)
        assert app.state.ga is None

    def test_create_app_title(self):
        """应用标题应为 ChatLens API"""
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=None)
        assert app.title == 'ChatLens API'

    def test_create_app_has_docs_url(self):
        """应用应配置 /docs 文档 URL"""
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=None)
        assert app.docs_url == '/docs'

    def test_create_app_has_routes(self):
        """应用应注册 API 路由"""
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=None)
        routes = [r.path for r in app.routes]
        assert '/api/health' in routes
        assert '/api/status' in routes
        assert '/api/groups' in routes

    def test_create_app_cors_middleware(self):
        """应用应配置 CORS 中间件"""
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=None)
        # 检查中间件栈中包含 CORSMiddleware
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert 'CORSMiddleware' in middleware_classes


# ═══════════════════════════════════════════════════════════
#  34. GET /api/docs — API 文档
# ═══════════════════════════════════════════════════════════

class TestGetDocs:
    def test_docs_ok(self):
        """获取 API 文档应返回成功"""
        web = _make_mock_web()
        web.get_api_docs.return_value = [{'path': '/api/health', 'method': 'GET'}]
        client = _create_client(web=web)
        resp = client.get('/api/docs')
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        assert len(resp.json()['endpoints']) == 1


# ═══════════════════════════════════════════════════════════
#  35. GET /api/data-files — 已加载数据文件
# ═══════════════════════════════════════════════════════════

class TestGetDataFiles:
    def test_data_files_ok(self):
        """获取数据文件应返回成功"""
        web = _make_mock_web()
        web.get_data_files.return_value = {'files': ['data1.db', 'data2.db']}
        client = _create_client(web=web)
        resp = client.get('/api/data-files')
        assert resp.status_code == 200
        assert 'files' in resp.json()


# ═══════════════════════════════════════════════════════════
#  36. GET /api/chatlog/chatrooms — 聊天室列表
# ═══════════════════════════════════════════════════════════

class TestGetChatrooms:
    def test_chatrooms_ok(self):
        """获取聊天室列表应返回成功"""
        web = _make_mock_web()
        web.get_chatlog_chatrooms.return_value = {'chatrooms': ['room1', 'room2']}
        client = _create_client(web=web)
        resp = client.get('/api/chatlog/chatrooms')
        assert resp.status_code == 200
        assert 'chatrooms' in resp.json()


# ═══════════════════════════════════════════════════════════
#  37. GET /api/chatlog/talkers — 联系人列表
# ═══════════════════════════════════════════════════════════

class TestGetTalkers:
    def test_talkers_ok(self):
        """获取联系人列表应返回成功"""
        web = _make_mock_web()
        web.get_chatlog_talkers.return_value = {'talkers': ['user1', 'user2']}
        client = _create_client(web=web)
        resp = client.get('/api/chatlog/talkers')
        assert resp.status_code == 200
        assert 'talkers' in resp.json()


# ═══════════════════════════════════════════════════════════
#  38. GET /api/chatlog/refresh — 刷新微信数据库
# ═══════════════════════════════════════════════════════════

class TestRefreshChatlog:
    def test_refresh_ok(self):
        """刷新微信数据库应返回成功"""
        web = _make_mock_web()
        web.refresh_chatlog.return_value = {'success': True, 'count': 5}
        client = _create_client(web=web)
        resp = client.get('/api/chatlog/refresh')
        assert resp.status_code == 200
        assert resp.json()['success'] is True


# ═══════════════════════════════════════════════════════════
#  39. POST /api/analysis/auto — 自动分析（含 start_date/end_date）
# ═══════════════════════════════════════════════════════════

class TestAutoAnalyze:
    def test_auto_analyze_ok(self):
        """自动分析应返回成功"""
        web = _make_mock_web()
        web.auto_analyze.return_value = {'success': True, 'data': {}}
        client = _create_client(web=web)
        resp = client.post('/api/analysis/auto', json={
            'group_name': 'g1', 'theme': 'scrapbook', 'fmt': 'jpg',
            'start_date': '2025-01-01', 'end_date': '2025-01-31'
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.auto_analyze.assert_called_once_with(
            'g1', theme='scrapbook', fmt='jpg',
            start_date='2025-01-01', end_date='2025-01-31',
            use_ide=False, use_rules=False, use_fallback=False
        )

    def test_auto_analyze_default_params(self):
        """自动分析默认参数应正确传递"""
        web = _make_mock_web()
        web.auto_analyze.return_value = {'success': True}
        client = _create_client(web=web)
        resp = client.post('/api/analysis/auto', json={'group_name': 'g1'})
        assert resp.status_code == 200
        web.auto_analyze.assert_called_once_with(
            'g1', theme='scrapbook', fmt='jpg',
            start_date='', end_date='',
            use_ide=False, use_rules=False, use_fallback=False
        )

    def test_auto_analyze_no_web(self):
        """服务未初始化时自动分析应返回失败"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/analysis/auto', json={'group_name': 'g1'})
        assert resp.json()['success'] is False

    def test_auto_analyze_passes_use_fallback_true(self):
        """Bug 修复：POST /api/analysis/auto 带 use_fallback=true 时透传到 web.auto_analyze"""
        web = _make_mock_web()
        web.auto_analyze.return_value = {'success': True, 'method': 'rules'}
        client = _create_client(web=web)
        resp = client.post('/api/analysis/auto', json={
            'group_name': 'g1', 'use_fallback': True
        })
        assert resp.status_code == 200
        call_kwargs = web.auto_analyze.call_args.kwargs
        assert call_kwargs['use_fallback'] is True
        assert call_kwargs['use_ide'] is False

    def test_auto_analyze_use_fallback_default_false(self):
        """Bug 修复：POST /api/analysis/auto 不带 use_fallback 时默认 False"""
        web = _make_mock_web()
        web.auto_analyze.return_value = {'success': True}
        client = _create_client(web=web)
        resp = client.post('/api/analysis/auto', json={'group_name': 'g1'})
        assert resp.status_code == 200
        call_kwargs = web.auto_analyze.call_args.kwargs
        assert call_kwargs['use_fallback'] is False


# ═══════════════════════════════════════════════════════════
#  40. POST /api/ide/task/create — 创建 IDE 分析任务
# ═══════════════════════════════════════════════════════════

class TestIdeTaskCreate:
    def test_create_ok(self):
        """创建 IDE 分析任务应返回成功"""
        web = _make_mock_web()
        web.create_ide_task.return_value = {'success': True, 'task_id': 'ide1'}
        client = _create_client(web=web)
        resp = client.post('/api/ide/task/create', json={
            'group_name': 'g1', 'theme': 'dark', 'fmt': 'png'
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.create_ide_task.assert_called_once_with('g1', theme='dark', fmt='png')

    def test_create_default_params(self):
        """创建 IDE 任务默认参数应正确传递"""
        web = _make_mock_web()
        web.create_ide_task.return_value = {'success': True, 'task_id': 'ide1'}
        client = _create_client(web=web)
        resp = client.post('/api/ide/task/create', json={'group_name': 'g1'})
        assert resp.status_code == 200
        web.create_ide_task.assert_called_once_with('g1', theme='scrapbook', fmt='jpg')

    def test_create_no_web(self):
        """服务未初始化时创建 IDE 任务应返回失败"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/ide/task/create', json={'group_name': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  41. GET /api/ide/tasks/pending — 待处理 IDE 任务
# ═══════════════════════════════════════════════════════════

class TestIdePendingTasks:
    def test_pending_ok(self):
        """获取待处理 IDE 任务应返回成功"""
        web = _make_mock_web()
        web.get_ide_pending_tasks.return_value = {'success': True, 'tasks': []}
        client = _create_client(web=web)
        resp = client.get('/api/ide/tasks/pending')
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    def test_pending_no_web(self):
        """服务未初始化时应返回失败"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/ide/tasks/pending')
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  42. GET /api/analysis/daily-dates — 每日日期列表
# ═══════════════════════════════════════════════════════════

class TestDailyDates:
    def test_daily_dates_ok(self):
        """获取每日日期列表应返回成功"""
        web = _make_mock_web()
        web.get_daily_dates.return_value = {'success': True, 'dates': ['2025-01-01']}
        client = _create_client(web=web)
        resp = client.get('/api/analysis/daily-dates', params={'group': 'g1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.get_daily_dates.assert_called_once_with('g1')

    def test_daily_dates_no_web(self):
        """服务未初始化时应返回失败"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/analysis/daily-dates', params={'group': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  43. POST /api/analysis/generate-image — 从 HTML 生成报告图片
# ═══════════════════════════════════════════════════════════

class TestGenerateImage:
    def test_generate_image_ok(self):
        """生成报告图片应返回成功"""
        report = _make_mock_report()
        report.generate_image_from_html = AsyncMock(
            return_value={'success': True, 'image_path': '/tmp/img.png'}
        )
        client = _create_client(report=report)
        resp = client.post('/api/analysis/generate-image', json={
            'html_file': '/tmp/report.html', 'fmt': 'png'
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        report.generate_image_from_html.assert_called_once_with('/tmp/report.html', fmt='png')

    def test_generate_image_no_report(self):
        """服务未初始化时应返回失败"""
        ga = _make_mock_ga()
        ga.web = _make_mock_web()
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/analysis/generate-image', json={'html_file': '/tmp/test.html'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  44. DELETE /api/data/delete — 删除群聊数据
# ═══════════════════════════════════════════════════════════

class TestDeleteData:
    def test_delete_ok(self):
        """删除群聊数据应返回成功"""
        web = _make_mock_web()
        web.delete_data.return_value = {'success': True}
        client = _create_client(web=web)
        resp = client.request('DELETE', '/api/data/delete', json={'group_name': 'g1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.delete_data.assert_called_once_with('g1')

    def test_delete_no_web(self):
        """服务未初始化时应返回失败"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.request('DELETE', '/api/data/delete', json={'group_name': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  44b. POST /api/data/batch-delete — F7 批量删除
# ═══════════════════════════════════════════════════════════

class TestBatchDeleteData:
    def test_batch_ok(self):
        """批量删除应正确传递 group_names 并返回成功"""
        web = _make_mock_web()
        web.delete_data_batch.return_value = {'success': True, 'deleted': 2, 'failed': 0}
        client = _create_client(web=web)
        resp = client.post('/api/data/batch-delete', json={'group_names': ['g1', 'g2']})
        assert resp.status_code == 200
        assert resp.json()['deleted'] == 2
        web.delete_data_batch.assert_called_once_with(['g1', 'g2'])

    def test_batch_empty_list(self):
        """空列表应正常返回"""
        web = _make_mock_web()
        web.delete_data_batch.return_value = {'success': True, 'deleted': 0, 'failed': 0}
        client = _create_client(web=web)
        resp = client.post('/api/data/batch-delete', json={'group_names': []})
        assert resp.status_code == 200
        assert resp.json()['deleted'] == 0
        web.delete_data_batch.assert_called_once_with([])

    def test_batch_no_web(self):
        """服务未初始化时应返回失败"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.post('/api/data/batch-delete', json={'group_names': ['g1']})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  45. DELETE /api/reports/delete — 删除报告文件
# ═══════════════════════════════════════════════════════════

class TestDeleteReport:
    def test_delete_ok(self):
        """删除报告文件应返回成功"""
        report = _make_mock_report()
        client = _create_client(report=report)
        resp = client.request('DELETE', '/api/reports/delete', json={'filename': 'report.html'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        report.delete_report.assert_called_once_with('report.html')

    def test_delete_no_report(self):
        """服务未初始化时应返回失败"""
        ga = _make_mock_ga()
        ga.web = _make_mock_web()
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.request('DELETE', '/api/reports/delete', json={'filename': 'report.html'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  46. GET /api/report/themes — 报告主题列表
# ═══════════════════════════════════════════════════════════

class TestReportThemes:
    def test_themes_ok(self):
        """获取报告主题列表应返回成功"""
        report = _make_mock_report()
        report.list_themes.return_value = ['classic', 'dark', 'scrapbook']
        client = _create_client(report=report)
        resp = client.get('/api/report/themes')
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        assert len(resp.json()['themes']) == 3

    def test_themes_no_report(self):
        """服务未初始化时应返回空主题列表"""
        ga = _make_mock_ga()
        ga.web = _make_mock_web()
        ga.report = None
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/report/themes')
        assert resp.json()['success'] is True
        assert resp.json()['themes'] == []


# ═══════════════════════════════════════════════════════════
#  47. GET /api/report — 获取报告数据
# ═══════════════════════════════════════════════════════════

class TestGetReport:
    def test_report_ok(self):
        """获取报告数据应返回成功"""
        web = _make_mock_web()
        web.generate_report.return_value = {'success': True, 'data': {'summary': 'test'}}
        client = _create_client(web=web)
        resp = client.get('/api/report', params={'group': 'g1'})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        web.generate_report.assert_called_once_with('g1')

    def test_report_no_group(self):
        """不传 group 参数应返回错误"""
        client = _create_client()
        resp = client.get('/api/report')
        assert resp.json()['success'] is False

    def test_report_no_web(self):
        """服务未初始化时应返回错误"""
        ga = _make_mock_ga()
        ga.web = None
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app)
        resp = client.get('/api/report', params={'group': 'g1'})
        assert resp.json()['success'] is False


# ═══════════════════════════════════════════════════════════
#  48. 全局异常处理
# ═══════════════════════════════════════════════════════════

class TestGlobalExceptionHandler:
    def test_exception_returns_500(self):
        """全局异常处理应返回 500 状态码"""
        ga = _make_mock_ga()
        ga.web = _make_mock_web()
        ga.report = _make_mock_report()
        from chatlens.plugins.web.async_app import create_app
        app = create_app(ga=ga)
        client = TestClient(app, raise_server_exceptions=False)
        # 使用不存在的路由触发 404，或通过其他方式触发异常
        # 由于 _run_sync 中的异常会穿透到中间件，我们用 raise_server_exceptions=False
        # 来让 TestClient 不抛异常，而是返回错误响应
        web = _make_mock_web()
        web.get_health.side_effect = RuntimeError("unexpected error")
        client = _create_client(web=web)
        # raise_server_exceptions=False 让 TestClient 返回错误响应而非抛异常
        client2 = TestClient(client.app, raise_server_exceptions=False)
        resp = client2.get('/api/health')
        assert resp.status_code == 500
        assert resp.json()['success'] is False

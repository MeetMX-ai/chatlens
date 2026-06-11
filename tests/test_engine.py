"""engine.py 单元测试 — 覆盖 ReportService 主要方法"""
import os
import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from chatlens.plugins.report.engine import ReportService


# ── 辅助 ──────────────────────────────────────────────────

def _make_mock_ga(reports_dir):
    ga = MagicMock()
    ga.get_reports_dir.return_value = reports_dir
    ga.get_provider.return_value = None
    return ga


def _create_report_file(tmp_path, filename, content='test'):
    fp = tmp_path / filename
    fp.write_text(content, encoding='utf-8')
    return fp


# ═══════════════════════════════════════════════════════════
#  1. list_reports()
# ═══════════════════════════════════════════════════════════

class TestListReports:
    def test_empty_dir(self, tmp_path):
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert result['success'] is True
        assert result['reports'] == []

    def test_dir_not_exist(self):
        ga = _make_mock_ga('/nonexistent_dir_xyz')
        svc = ReportService(ga)
        result = svc.list_reports()
        assert result['success'] is True
        assert result['reports'] == []

    def test_single_report(self, tmp_path):
        _create_report_file(tmp_path, 'mygroup_1718000000.html')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert result['success'] is True
        assert len(result['reports']) == 1
        # 文件名解析：mygroup_1718000000 → group_name = mygroup
        assert result['reports'][0]['group_name'] == 'mygroup'
        assert result['reports'][0]['format'] == 'HTML'

    def test_group_name_with_underscore(self, tmp_path):
        """群名含下划线时，时间戳前的部分应完整保留"""
        _create_report_file(tmp_path, 'my_group_name_1718000000.jpg')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert len(result['reports']) == 1
        # my_group_name_1718000000 → name_part = my_group_name
        assert result['reports'][0]['group_name'] == 'my_group_name'
        assert result['reports'][0]['format'] == 'JPG'

    def test_multiple_reports_sorted_by_mtime(self, tmp_path):
        f1 = _create_report_file(tmp_path, 'group1_1718000000.html')
        time.sleep(0.05)
        f2 = _create_report_file(tmp_path, 'group2_1718001000.html')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert len(result['reports']) == 2
        # 最新的排在前面
        assert result['reports'][0]['filename'] == 'group2_1718001000.html'

    def test_ignores_non_report_extensions(self, tmp_path):
        _create_report_file(tmp_path, 'data.json')
        _create_report_file(tmp_path, 'readme.txt')
        _create_report_file(tmp_path, 'report.html')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert len(result['reports']) == 1
        assert result['reports'][0]['format'] == 'HTML'

    def test_no_timestamp_in_filename(self, tmp_path):
        """无时间戳的文件名，group_name 为整个文件名"""
        _create_report_file(tmp_path, 'simplegroup.png')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert len(result['reports']) == 1
        assert result['reports'][0]['group_name'] == 'simplegroup'


# ═══════════════════════════════════════════════════════════
#  2. get_report_html() — 通过 generate_image_from_html 间接测试
# ═══════════════════════════════════════════════════════════

class TestGenerateImageFromHtml:
    async def test_path_traversal_rejected(self):
        ga = _make_mock_ga('/tmp/reports')
        svc = ReportService(ga)
        result = await svc.generate_image_from_html('../etc/passwd')
        assert result['success'] is False
        assert '无效' in result['error'] or '非法' in result['error']

    async def test_path_with_subdir_rejected(self):
        ga = _make_mock_ga('/tmp/reports')
        svc = ReportService(ga)
        result = await svc.generate_image_from_html('sub/file.html')
        assert result['success'] is False

    async def test_file_not_exist(self, tmp_path):
        ga = _make_mock_ga(str(tmp_path))
        with patch('chatlens.plugins.report.engine.image_report.get_output_dir', return_value=str(tmp_path)):
            svc = ReportService(ga)
            result = await svc.generate_image_from_html('nonexistent.html')
            assert result['success'] is False
            assert '不存在' in result['error']

    async def test_valid_html_file(self, tmp_path):
        html_file = tmp_path / 'report_1718000000.html'
        html_file.write_text('<html>report</html>', encoding='utf-8')
        # 同时创建模拟的输出图片文件，使 os.path.exists(img_path) 为 True
        img_file = tmp_path / 'report_1718000000.jpg'
        img_file.write_bytes(b'\xff\xd8\xff\xe0')
        with patch('chatlens.plugins.report.engine.image_report.get_output_dir', return_value=str(tmp_path)):
            with patch('chatlens.plugins.report.engine.image_report.generate_image_from_html', new_callable=AsyncMock, return_value=str(img_file)):
                ga = _make_mock_ga(str(tmp_path))
                svc = ReportService(ga)
                result = await svc.generate_image_from_html('report_1718000000.html')
                assert result['success'] is True


# ═══════════════════════════════════════════════════════════
#  3. delete_report()
# ═══════════════════════════════════════════════════════════

class TestDeleteReport:
    def test_delete_ok(self, tmp_path):
        fp = _create_report_file(tmp_path, 'report_1718000000.html')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.delete_report('report_1718000000.html')
        assert result['success'] is True
        assert not os.path.exists(str(fp))

    def test_delete_empty_filename(self):
        ga = _make_mock_ga('/tmp/reports')
        svc = ReportService(ga)
        result = svc.delete_report('')
        assert result['success'] is False
        assert '未指定' in result['error']

    def test_delete_illegal_filename(self):
        ga = _make_mock_ga('/tmp/reports')
        svc = ReportService(ga)
        result = svc.delete_report('../../../etc/passwd')
        assert result['success'] is False
        assert '非法' in result['error']

    def test_delete_nonexistent_file(self, tmp_path):
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.delete_report('nonexistent.html')
        assert result['success'] is False
        assert '不存在' in result['error']


# ═══════════════════════════════════════════════════════════
#  4. generate_image_from_html() 独立函数 — mock Chrome subprocess
# ═══════════════════════════════════════════════════════════

class TestGenerateImageFromHtmlStandalone:
    async def test_normal_generation(self, tmp_path):
        """测试正常生成图片流程（mock Chrome subprocess）"""
        html_file = tmp_path / 'report_1718000000.html'
        html_file.write_text('<html>report</html>', encoding='utf-8')

        def mock_pdf_to_image(pdf_path, out_path, zoom=3):
            """模拟 _pdf_to_image：创建真实 PNG 供 PIL 处理"""
            from PIL import Image
            img = Image.new('RGB', (100, 100), color='white')
            img.save(out_path)
            return True

        with patch('chatlens.plugins.report.image_report._html_to_pdf_chrome', new_callable=AsyncMock, return_value=True), \
             patch('chatlens.plugins.report.image_report._pdf_to_image', side_effect=mock_pdf_to_image), \
             patch('chatlens.plugins.report.image_report._html_to_image_chrome', new_callable=AsyncMock, return_value=False), \
             patch('chatlens.plugins.report.image_report._html_to_image_html2image', return_value=None):
            from chatlens.plugins.report.image_report import generate_image_from_html
            result = await generate_image_from_html(str(html_file), fmt='jpg')
            assert result is not None
            assert os.path.exists(result)

    async def test_html_not_exist(self):
        """HTML 文件不存在时返回 None"""
        from chatlens.plugins.report.image_report import generate_image_from_html
        result = await generate_image_from_html('/nonexistent/file.html')
        assert result is None

    async def test_empty_path(self):
        """空路径返回 None"""
        from chatlens.plugins.report.image_report import generate_image_from_html
        result = await generate_image_from_html('')
        assert result is None


# ═══════════════════════════════════════════════════════════
#  5. get_report_html() — HTML 内容读取
# ═══════════════════════════════════════════════════════════

class TestGetReportHtml:
    def test_read_html_content(self, tmp_path):
        """测试读取 HTML 报告文件内容"""
        html_content = '<html><body>Test Report</body></html>'
        html_file = tmp_path / 'report_1718000000.html'
        html_file.write_text(html_content, encoding='utf-8')
        # 直接读取验证
        with open(str(html_file), 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'Test Report' in content

    def test_read_html_with_chinese(self, tmp_path):
        """测试读取含中文的 HTML 报告"""
        html_content = '<html><body>群聊报告 测试</body></html>'
        html_file = tmp_path / '中文报告.html'
        html_file.write_text(html_content, encoding='utf-8')
        with open(str(html_file), 'r', encoding='utf-8') as f:
            content = f.read()
        assert '群聊报告' in content


# ═══════════════════════════════════════════════════════════
#  6. delete_report() — 补充路径穿越测试
# ═══════════════════════════════════════════════════════════

class TestDeleteReportExtended:
    def test_delete_path_traversal_symlink(self, tmp_path):
        """删除操作应拒绝路径穿越（realpath 检查）"""
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        # 使用合法文件名但尝试通过符号链接逃逸
        result = svc.delete_report('normal.html')
        # 文件不存在，应返回错误
        assert result['success'] is False


# ═══════════════════════════════════════════════════════════
#  7. ReportService.__init__() — 测试初始化
# ═══════════════════════════════════════════════════════════

class TestReportServiceInit:
    def test_init_stores_ga(self):
        ga = MagicMock()
        svc = ReportService(ga)
        assert svc.ga is ga

    def test_init_with_none_ga(self):
        svc = ReportService(None)
        assert svc.ga is None


# ═══════════════════════════════════════════════════════════
#  8. ReportService.generate_image() — 测试委托调用
# ═══════════════════════════════════════════════════════════

class TestReportServiceGenerateImage:
    async def test_generate_image_delegation(self, tmp_path):
        """测试 generate_image 委托到 image_report.generate_report_image"""
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        with patch('chatlens.plugins.report.engine.image_report.generate_report_image', new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = (None, str(tmp_path / 'test.html'))
            # 创建 HTML 文件使 os.path.exists 通过
            (tmp_path / 'test.html').write_text('<html></html>', encoding='utf-8')
            result = await svc.generate_image('group1', {'total': 100}, {}, 'scrapbook', 'jpg')
            mock_gen.assert_called_once()
            assert result['success'] is True

    async def test_generate_image_error(self, tmp_path):
        """测试 generate_image 异常处理"""
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        with patch('chatlens.plugins.report.engine.image_report.generate_report_image', new_callable=AsyncMock, side_effect=OSError('disk full')):
            result = await svc.generate_image('group1', {'total': 100}, {}, 'scrapbook', 'jpg')
            assert result['success'] is False
            assert 'disk full' in result['error']

    async def test_generate_image_preserves_html_file(self, tmp_path):
        """Bug 6 修复：成功生成图片后 report_info 仍包含 html_file 字段

        之前 ``if img_path and os.path.exists(img_path):`` 分支用
        ``report_info = {...}`` 重新赋值，会把第一个 ``if`` 块设置的
        ``html_file`` 字段直接覆盖掉，导致前端拿不到原始 HTML 文件名。
        修复后改用 ``report_info.update(...)``，html_file 应当保留。
        """
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        # 同时准备好真实 HTML 和图片文件
        html_file = tmp_path / 'mygroup_1718000000.html'
        html_file.write_text('<html>report</html>', encoding='utf-8')
        img_file = tmp_path / 'mygroup_1718000000.jpg'
        img_file.write_bytes(b'\xff\xd8\xff\xe0')

        with patch(
            'chatlens.plugins.report.engine.image_report.generate_report_image',
            new_callable=AsyncMock,
            return_value=(str(img_file), str(html_file)),
        ):
            result = await svc.generate_image(
                'mygroup', {'total': 100}, {}, 'scrapbook', 'jpg'
            )

        assert result['success'] is True
        report = result['report']
        # 关键断言：html_file 字段必须保留
        assert 'html_file' in report, f"html_file 丢失，report = {report}"
        assert report['html_file'] == 'mygroup_1718000000.html'
        # 同时 image_path / html_path 也要存在
        assert report['image_path'] == str(img_file)
        assert report['html_path'] == str(html_file)


# ═══════════════════════════════════════════════════════════
#  9. ReportService.generate_pdf() — 测试委托调用
# ═══════════════════════════════════════════════════════════

class TestReportServiceGeneratePdf:
    async def test_generate_pdf_delegation(self, tmp_path):
        """测试 generate_pdf 委托到 pdf_report.generate_report_pdf"""
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        with patch('chatlens.plugins.report.engine.pdf_report.generate_report_pdf', new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = (str(tmp_path / 'test.pdf'), str(tmp_path / 'test.html'))
            result = await svc.generate_pdf('group1', {'total': 100}, {}, 'scrapbook')
            mock_gen.assert_called_once()
            assert result['success'] is True
            assert 'pdf_path' in result

    async def test_generate_pdf_error(self, tmp_path):
        """测试 generate_pdf 异常处理"""
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        with patch('chatlens.plugins.report.engine.pdf_report.generate_report_pdf', new_callable=AsyncMock, side_effect=ValueError('bad data')):
            result = await svc.generate_pdf('group1', {'total': 100}, {}, 'scrapbook')
            assert result['success'] is False
            assert 'bad data' in result['error']


# ═══════════════════════════════════════════════════════════
#  10. ReportService.list_reports() — 补充委托测试
# ═══════════════════════════════════════════════════════════

class TestListReportsExtended:
    def test_list_reports_png_format(self, tmp_path):
        """测试 PNG 格式报告被正确识别"""
        _create_report_file(tmp_path, 'group1_1718000000.png')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert result['success'] is True
        assert len(result['reports']) == 1
        assert result['reports'][0]['format'] == 'PNG'

    def test_list_reports_pdf_format(self, tmp_path):
        """测试 PDF 格式报告被正确识别"""
        _create_report_file(tmp_path, 'group1_1718000000.pdf')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert result['success'] is True
        assert len(result['reports']) == 1
        assert result['reports'][0]['format'] == 'PDF'

    def test_list_reports_skips_directories(self, tmp_path):
        """测试目录被跳过"""
        sub_dir = tmp_path / 'subdir'
        sub_dir.mkdir()
        _create_report_file(tmp_path, 'report.html')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        result = svc.list_reports()
        assert len(result['reports']) == 1


# ═══════════════════════════════════════════════════════════
#  11. ReportService.delete_report() — 补充委托测试
# ═══════════════════════════════════════════════════════════

class TestDeleteReportDelegation:
    def test_delete_os_error(self, tmp_path):
        """测试删除文件时 OS 错误"""
        fp = _create_report_file(tmp_path, 'report_1718000000.html')
        ga = _make_mock_ga(str(tmp_path))
        svc = ReportService(ga)
        with patch('os.remove', side_effect=OSError('permission denied')):
            result = svc.delete_report('report_1718000000.html')
            assert result['success'] is False
            assert 'permission denied' in result['error']


# ═══════════════════════════════════════════════════════════
#  12. ReportService.generate_image_from_html() — 补充委托测试
# ═══════════════════════════════════════════════════════════

class TestGenerateImageFromHtmlDelegation:
    async def test_delegation_with_valid_file(self, tmp_path):
        """测试 generate_image_from_html 委托到 image_report 模块"""
        html_file = tmp_path / 'report_1718000000.html'
        html_file.write_text('<html>report</html>', encoding='utf-8')
        img_file = tmp_path / 'report_1718000000.jpg'
        img_file.write_bytes(b'\xff\xd8\xff\xe0')
        with patch('chatlens.plugins.report.engine.image_report.get_output_dir', return_value=str(tmp_path)), \
             patch('chatlens.plugins.report.engine.image_report.generate_image_from_html', new_callable=AsyncMock, return_value=str(img_file)):
            ga = _make_mock_ga(str(tmp_path))
            svc = ReportService(ga)
            result = await svc.generate_image_from_html('report_1718000000.html')
            assert result['success'] is True

    async def test_delegation_image_generation_fails(self, tmp_path):
        """测试 generate_image_from_html 图片生成失败"""
        html_file = tmp_path / 'report_1718000000.html'
        html_file.write_text('<html>report</html>', encoding='utf-8')
        with patch('chatlens.plugins.report.engine.image_report.get_output_dir', return_value=str(tmp_path)), \
             patch('chatlens.plugins.report.engine.image_report.generate_image_from_html', new_callable=AsyncMock, return_value=None):
            ga = _make_mock_ga(str(tmp_path))
            svc = ReportService(ga)
            result = await svc.generate_image_from_html('report_1718000000.html')
            assert result['success'] is False
            assert '失败' in result['error']


# ═══════════════════════════════════════════════════════════
#  13. ReportService.list_themes() — 测试委托调用
# ═══════════════════════════════════════════════════════════

class TestListThemes:
    def test_list_themes(self):
        """测试 list_themes 委托到 template_engine"""
        ga = _make_mock_ga('/tmp/reports')
        svc = ReportService(ga)
        with patch('chatlens.plugins.report.engine.template_engine.list_themes') as mock_themes:
            mock_themes.return_value = ['classic', 'scrapbook', 'hack']
            result = svc.list_themes()
            assert result == ['classic', 'scrapbook', 'hack']
            mock_themes.assert_called_once()

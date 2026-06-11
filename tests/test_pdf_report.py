"""pdf_report.py 单元测试 — mock Chrome 依赖，覆盖核心逻辑"""
import os
import asyncio
import tempfile
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from chatlens.plugins.report.pdf_report import (
    _html_to_pdf_chrome,
    generate_report_pdf,
)


# ═══════════════════════════════════════════════════════════
#  1. _html_to_pdf_chrome()
# ═══════════════════════════════════════════════════════════

class TestHtmlToPdfChrome:
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value=None)
    async def test_no_chrome_returns_false(self, mock_find):
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is False

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_success(self, mock_find, mock_exec, mock_exists, mock_size):
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is True
        mock_exec.assert_called_once()

    @patch("os.path.exists", return_value=False)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_pdf_not_created_returns_false(self, mock_find, mock_exec, mock_exists):
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is False

    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_timeout_returns_false(self, mock_find):
        with patch(
            "chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_proc.kill = MagicMock()
            mock_exec.return_value = mock_proc
            mock_exec.return_value.wait = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_proc.kill = MagicMock()
            mock_proc.wait = AsyncMock()
            result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
            assert result is False

    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_generic_exception_returns_false(self, mock_find):
        with patch(
            "chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=OSError("broken"),
        ):
            result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
            assert result is False


# ═══════════════════════════════════════════════════════════
#  2. 安全标志验证
# ═══════════════════════════════════════════════════════════

class TestChromeSecurityFlags:
    """确认 Chrome 命令包含安全标志"""

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_security_flags_present(self, mock_find, mock_exec, mock_exists, mock_size):
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        cmd = mock_exec.call_args[0]
        # 验证关键安全标志
        assert "--headless" in cmd
        assert "--disable-gpu" in cmd
        assert "--no-sandbox" in cmd
        assert "--disable-extensions" in cmd
        assert "--disable-software-rasterizer" in cmd
        assert "--disable-dev-shm-usage" in cmd
        assert "--disable-remote-fonts" in cmd

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_pdf_output_flags(self, mock_find, mock_exec, mock_exists, mock_size):
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        cmd = mock_exec.call_args[0]
        # 验证 PDF 输出标志
        pdf_flag = [a for a in cmd if a.startswith("--print-to-pdf=")]
        assert len(pdf_flag) == 1
        assert "--print-to-pdf-no-header" in cmd

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_file_url_format(self, mock_find, mock_exec, mock_exists, mock_size):
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        cmd = mock_exec.call_args[0]
        # 最后一个参数应该是 file:/// URL
        url_arg = cmd[-1]
        assert url_arg.startswith("file:///")


# ═══════════════════════════════════════════════════════════
#  3. generate_report_pdf()
# ═══════════════════════════════════════════════════════════

def _sample_stats():
    return {
        "overview": {
            "total_messages": 1000,
            "total_members": 50,
            "avg_messages_per_day": 30,
            "time_range": {"start": "2025-01-01", "end": "2025-06-01"},
        },
        "member_stats": [
            {"sender": "Alice", "msg_count": 200, "msg_percentage": 20, "avg_chars_per_msg": 15},
            {"sender": "Bob", "msg_count": 150, "msg_percentage": 15, "avg_chars_per_msg": 10},
        ],
        "keyword_cloud": [{"word": "hello", "count": 50}],
        "hourly_distribution": [{"label": "08", "count": 10}],
        "daily_trend": [{"date": "2025-01-15", "count": 30}],
        "msg_type_distribution": [{"type": "text", "count": 50}],
    }


def _sample_ai_data():
    return {
        "summary": {"summary": "Test summary", "topics": ["topic1"]},
        "user_titles": {"user_titles": [{"name": "Alice", "title": "话痨"}]},
        "golden_quotes": {"golden_quotes": [{"sender": "Bob", "quote": "Hello world"}]},
        "chat_quality": {
            "title": "质量评估",
            "subtitle": "副标题",
            "dimensions": [{"name": "活跃度", "percentage": 80, "comment": "高", "color": "#e07850"}],
            "summary": "总体不错",
        },
        "keywords": {"keywords": [{"word": "test"}]},
    }


class TestGenerateReportPdf:
    @patch("chatlens.plugins.report.pdf_report.template_engine")
    async def test_render_returns_none(self, mock_te):
        mock_te.render.return_value = None
        pdf, html = await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data())
        assert pdf is None
        assert html is None

    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False)
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False)
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False)
    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_all_pdf_methods_fail_returns_html(self, mock_dir, mock_te, mock_chrome, mock_pw, mock_wp):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            pdf, html = await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data())
        assert pdf is None
        assert html is not None

    @patch("chatlens.plugins.report.pdf_report._crop_pdf", return_value=True)
    @patch("shutil.move")
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=True)
    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_chrome_pdf_success(self, mock_dir, mock_te, mock_chrome, mock_move, mock_crop):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            pdf, html = await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data())
        assert pdf is not None
        assert html is not None
        mock_chrome.assert_called_once()
        mock_crop.assert_called_once()

    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False)
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=True)
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False)
    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_playwright_fallback(self, mock_dir, mock_te, mock_chrome, mock_pw, mock_wp):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            pdf, html = await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data())
        assert pdf is not None
        assert html is not None
        mock_pw.assert_called_once()

    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=True)
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False)
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False)
    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_weasyprint_fallback(self, mock_dir, mock_te, mock_chrome, mock_pw, mock_wp):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            pdf, html = await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data())
        assert pdf is not None
        assert html is not None
        mock_wp.assert_called_once()

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_safe_name_sanitization(self, mock_dir, mock_te):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            pdf, html = await generate_report_pdf('test<>"group', _sample_stats(), _sample_ai_data())
        basename = os.path.basename(html)
        # pdf_report 的 safe_name 只替换 / \ : ，不替换 <>
        # 但至少不应包含 / \ :
        assert "/" not in basename
        assert "\\" not in basename
        assert ":" not in basename

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_safe_name_filters_windows_illegal_chars(self, mock_dir, mock_te):
        """Bug 4 修复：safe_name 应过滤 Windows 全部非法字符 < > | ? *"""
        mock_te.render.return_value = "<html>report</html>"
        for ch in '<>|?*':
            with patch("builtins.open", MagicMock()), \
                 patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
                 patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
                 patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
                pdf, html = await generate_report_pdf(
                    f"a{ch}b", _sample_stats(), _sample_ai_data()
                )
            basename = os.path.basename(html)
            assert ch not in basename, f"字符 {ch!r} 未被清洗：{basename}"
            # 替换为下划线
            assert "_" in basename

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_sanitize_filename_unit(self, mock_dir, mock_te):
        """sanitize_filename 单元测试：< > | ? * 全部转 _"""
        from chatlens.utils.strings import sanitize_filename

        for ch in '<>|?*':
            assert sanitize_filename(f"a{ch}b") == "a_b", f"字符 {ch!r} 未转 _"
        # 多个非法字符
        assert sanitize_filename("a<b>c|d?e*f") == "a_b_c_d_e_f"
        # 微信群常见后缀
        assert "chatroom" not in sanitize_filename("group@chatroom")
        # 空字符串 / 全非法字符应退回到 'report'
        assert sanitize_filename("") == "report"
        assert sanitize_filename("<<<>>>") == "report"
        # None 也安全
        assert sanitize_filename(None) == "report"

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_chatroom_suffix_removed(self, mock_dir, mock_te):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            pdf, html = await generate_report_pdf("mygroup@chatroom", _sample_stats(), _sample_ai_data())
        basename = os.path.basename(html)
        assert "@chatroom" not in basename

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_empty_member_stats(self, mock_dir, mock_te):
        stats = _sample_stats()
        stats["member_stats"] = []
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            pdf, html = await generate_report_pdf("test_group", stats, _sample_ai_data())
        assert html is not None


# ═══════════════════════════════════════════════════════════
#  4. Chrome 命令行参数完整性（所有安全标志逐一验证）
# ═══════════════════════════════════════════════════════════

class TestChromeCommandLineCompleteness:
    """验证 Chrome 命令行包含所有安全标志和输出参数"""

    EXPECTED_FLAGS = [
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-extensions",
        "--disable-software-rasterizer",
        "--disable-dev-shm-usage",
        "--disable-remote-fonts",
        "--print-to-pdf-no-header",
    ]

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_all_security_flags_present(self, mock_find, mock_exec, mock_exists, mock_size):
        """逐一验证每个安全标志都存在于命令行中"""
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        cmd = mock_exec.call_args[0]
        for flag in self.EXPECTED_FLAGS:
            assert flag in cmd, f"缺少安全标志: {flag}"

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_print_to_pdf_flag_has_path(self, mock_find, mock_exec, mock_exists, mock_size):
        """--print-to-pdf= 标志包含目标 PDF 路径"""
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        cmd = mock_exec.call_args[0]
        pdf_flags = [a for a in cmd if a.startswith("--print-to-pdf=")]
        assert len(pdf_flags) == 1
        assert "/tmp/test.pdf" in pdf_flags[0] or "test.pdf" in pdf_flags[0]

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_chrome_exe_is_first_arg(self, mock_find, mock_exec, mock_exists, mock_size):
        """Chrome 可执行文件是命令行第一个参数"""
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        cmd = mock_exec.call_args[0]
        assert cmd[0] == "C:\\Chrome\\chrome.exe"

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_no_duplicate_flags(self, mock_find, mock_exec, mock_exists, mock_size):
        """命令行中无重复标志"""
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        cmd = mock_exec.call_args[0]
        # 排除 Chrome 路径和 URL
        flags_only = [a for a in cmd[1:] if not a.startswith("file:///")]
        assert len(flags_only) == len(set(flags_only)), f"存在重复标志: {flags_only}"


# ═══════════════════════════════════════════════════════════
#  5. generate_report_pdf() — 不同主题参数
# ═══════════════════════════════════════════════════════════

class TestGenerateReportPdfThemes:
    """测试 generate_report_pdf() 不同主题参数"""

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_scrapbook_theme(self, mock_dir, mock_te):
        """scrapbook 主题被传递给模板引擎"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data(), theme="scrapbook")
        mock_te.render.assert_called_once()
        assert mock_te.render.call_args[0][0] == "scrapbook"

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_classic_theme(self, mock_dir, mock_te):
        """classic 主题被传递给模板引擎"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data(), theme="classic")
        assert mock_te.render.call_args[0][0] == "classic"

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_dark_theme(self, mock_dir, mock_te):
        """dark 主题被传递给模板引擎"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data(), theme="dark")
        assert mock_te.render.call_args[0][0] == "dark"


# ═══════════════════════════════════════════════════════════
#  6. generate_report_pdf() — 空统计数据
# ═══════════════════════════════════════════════════════════

class TestGenerateReportPdfEmptyStats:
    """测试 generate_report_pdf() 空统计数据"""

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_empty_overview(self, mock_dir, mock_te):
        """overview 为空时不崩溃"""
        mock_te.render.return_value = "<html>report</html>"
        stats = {"overview": {}, "member_stats": []}
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            pdf, html = await generate_report_pdf("test_group", stats, {})
        assert html is not None
        # 验证模板引擎收到零值
        render_kwargs = mock_te.render.call_args[1]
        assert render_kwargs.get("total_messages", 0) == 0
        assert render_kwargs.get("total_members", 0) == 0

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_completely_empty_stats(self, mock_dir, mock_te):
        """stats 完全为空字典时不崩溃"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            pdf, html = await generate_report_pdf("test_group", {}, {})
        assert html is not None

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_empty_ai_data(self, mock_dir, mock_te):
        """ai_data 为空时不崩溃"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            pdf, html = await generate_report_pdf("test_group", _sample_stats(), {})
        assert html is not None
        render_kwargs = mock_te.render.call_args[1]
        assert render_kwargs.get("ai_summary") == ""
        assert render_kwargs.get("ai_titles") == []


# ═══════════════════════════════════════════════════════════
#  7. generate_report_pdf() — 安全文件名清洗
# ═══════════════════════════════════════════════════════════

class TestGenerateReportPdfSafeFilename:
    """测试 generate_report_pdf() 安全文件名清洗"""

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_slash_replaced(self, mock_dir, mock_te):
        """群名中的 / 被替换为 _"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()) as mock_open, \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("group/name", _sample_stats(), _sample_ai_data())
        html_path = mock_open.call_args[0][0]
        basename = os.path.basename(html_path)
        assert "/" not in basename
        assert "_" in basename

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_backslash_replaced(self, mock_dir, mock_te):
        """群名中的 \\ 被替换为 _"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()) as mock_open, \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("group\\name", _sample_stats(), _sample_ai_data())
        html_path = mock_open.call_args[0][0]
        basename = os.path.basename(html_path)
        assert "\\" not in basename

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_colon_replaced(self, mock_dir, mock_te):
        """群名中的 : 被替换为 _"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()) as mock_open, \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("group:name", _sample_stats(), _sample_ai_data())
        html_path = mock_open.call_args[0][0]
        basename = os.path.basename(html_path)
        assert ":" not in basename

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_multiple_dangerous_chars(self, mock_dir, mock_te):
        """群名包含多种危险字符时全部被清洗"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()) as mock_open, \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("a/b\\c:d@chatroom", _sample_stats(), _sample_ai_data())
        html_path = mock_open.call_args[0][0]
        basename = os.path.basename(html_path)
        assert "/" not in basename
        assert "\\" not in basename
        assert ":" not in basename
        assert "@chatroom" not in basename


# ═══════════════════════════════════════════════════════════
#  8. _html_to_pdf_chrome() — 自定义 Chrome 路径
# ═══════════════════════════════════════════════════════════

class TestHtmlToPdfChromeCustomPath:
    """测试 _html_to_pdf_chrome() 自定义 Chrome 路径"""

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="/opt/chrome/chrome")
    async def test_custom_chrome_path_linux(self, mock_find, mock_exec, mock_exists, mock_size):
        """自定义 Linux Chrome 路径被正确使用"""
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is True
        cmd = mock_exec.call_args[0]
        assert cmd[0] == "/opt/chrome/chrome"

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="D:\\Apps\\Chrome\\chrome.exe")
    async def test_custom_chrome_path_windows(self, mock_find, mock_exec, mock_exists, mock_size):
        """自定义 Windows Chrome 路径被正确使用"""
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is True
        cmd = mock_exec.call_args[0]
        assert cmd[0] == "D:\\Apps\\Chrome\\chrome.exe"

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.pdf_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value="/usr/bin/chromium-browser")
    async def test_custom_chromium_path(self, mock_find, mock_exec, mock_exists, mock_size):
        """Chromium 路径被正确使用"""
        mock_exec.return_value.wait = AsyncMock(return_value=0)
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is True
        cmd = mock_exec.call_args[0]
        assert cmd[0] == "/usr/bin/chromium-browser"

    @patch("chatlens.plugins.report.pdf_report._find_chrome", return_value=None)
    async def test_no_chrome_found_returns_false(self, mock_find):
        """_find_chrome 返回 None 时返回 False"""
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is False


# ═══════════════════════════════════════════════════════════
#  9. _find_chrome() — Chrome 查找逻辑
# ═══════════════════════════════════════════════════════════

class TestFindChrome:
    """测试 _find_chrome() Chrome 查找逻辑"""

    @patch("shutil.which", return_value=None)
    @patch("os.path.isfile", return_value=False)
    def test_no_chrome_returns_none(self, mock_isfile, mock_which):
        """未找到 Chrome 时返回 None"""
        from chatlens.plugins.report.image_report import _find_chrome
        result = _find_chrome()
        assert result is None

    @patch("shutil.which", return_value=None)
    @patch("os.path.isfile", return_value=True)
    def test_found_in_program_files(self, mock_isfile, mock_which):
        """在 Program Files 中找到 Chrome"""
        from chatlens.plugins.report.image_report import _find_chrome
        result = _find_chrome()
        assert result is not None

    @patch("shutil.which", return_value="/usr/bin/chromium-browser")
    @patch("os.path.isfile", return_value=False)
    def test_found_via_which(self, mock_isfile, mock_which):
        """通过 shutil.which 找到 Chrome"""
        from chatlens.plugins.report.image_report import _find_chrome
        result = _find_chrome()
        assert result == "/usr/bin/chromium-browser"

    @patch("shutil.which", return_value=None)
    @patch("os.path.isfile")
    def test_prefers_first_candidate(self, mock_isfile, mock_which):
        """优先返回第一个候选路径"""
        from chatlens.plugins.report.image_report import _find_chrome
        # 第一个 isfile 调用返回 True
        mock_isfile.return_value = True
        result = _find_chrome()
        assert result is not None
        # 确认只检查到第一个匹配就返回
        assert mock_isfile.call_count == 1


# ═══════════════════════════════════════════════════════════
#  10. _html_to_pdf_playwright() — Playwright PDF 生成
# ═══════════════════════════════════════════════════════════

class TestHtmlToPdfPlaywright:
    """测试 _html_to_pdf_playwright()"""

    @staticmethod
    def _make_mock_playwright(mock_browser=None, mock_page=None):
        """构建 mock playwright 模块"""
        if mock_page is None:
            mock_page = MagicMock()
            mock_page.evaluate.return_value = 960
        if mock_browser is None:
            mock_browser = MagicMock()
            mock_browser.new_page.return_value = mock_page
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_sync_api = MagicMock()
        mock_sync_api.sync_playwright.return_value.__enter__ = MagicMock(return_value=mock_pw_instance)
        mock_sync_api.sync_playwright.return_value.__exit__ = MagicMock(return_value=False)
        return mock_sync_api

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    def test_playwright_success(self, mock_exists, mock_size):
        """Playwright 成功生成 PDF"""
        from chatlens.plugins.report.pdf_report import _html_to_pdf_playwright
        mock_sync_api = self._make_mock_playwright()
        with patch.dict("sys.modules", {"playwright": MagicMock(), "playwright.sync_api": mock_sync_api}):
            result = _html_to_pdf_playwright("/tmp/test.html", "/tmp/test.pdf")
        assert result is True
        mock_sync_api.sync_playwright.return_value.__enter__.return_value.chromium.launch.return_value.new_page.return_value.pdf.assert_called_once()

    def test_playwright_import_error(self):
        """Playwright 未安装时返回 False"""
        from chatlens.plugins.report.pdf_report import _html_to_pdf_playwright
        with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
            result = _html_to_pdf_playwright("/tmp/test.html", "/tmp/test.pdf")
        assert result is False

    @patch("os.path.exists", return_value=False)
    def test_playwright_pdf_not_created(self, mock_exists):
        """Playwright 生成的 PDF 不存在时返回 False"""
        from chatlens.plugins.report.pdf_report import _html_to_pdf_playwright
        mock_sync_api = self._make_mock_playwright()
        with patch.dict("sys.modules", {"playwright": MagicMock(), "playwright.sync_api": mock_sync_api}):
            result = _html_to_pdf_playwright("/tmp/test.html", "/tmp/test.pdf")
        assert result is False

    def test_playwright_exception_returns_false(self):
        """Playwright 抛出异常时返回 False"""
        from chatlens.plugins.report.pdf_report import _html_to_pdf_playwright
        mock_browser = MagicMock()
        mock_browser.new_page.side_effect = RuntimeError("launch failed")
        mock_sync_api = self._make_mock_playwright(mock_browser=mock_browser)
        with patch.dict("sys.modules", {"playwright": MagicMock(), "playwright.sync_api": mock_sync_api}):
            result = _html_to_pdf_playwright("/tmp/test.html", "/tmp/test.pdf")
        assert result is False


# ═══════════════════════════════════════════════════════════
#  11. _html_to_pdf_weasyprint() — WeasyPrint PDF 生成
# ═══════════════════════════════════════════════════════════

class TestHtmlToPdfWeasyPrint:
    """测试 _html_to_pdf_weasyprint()"""

    @patch("os.path.getsize", return_value=4096)
    @patch("os.path.exists", return_value=True)
    def test_weasyprint_success(self, mock_exists, mock_size):
        """WeasyPrint 成功生成 PDF"""
        from chatlens.plugins.report.pdf_report import _html_to_pdf_weasyprint
        mock_html = MagicMock()
        with patch.dict("sys.modules", {"weasyprint": MagicMock(HTML=MagicMock(return_value=mock_html))}):
            result = _html_to_pdf_weasyprint("/tmp/test.html", "/tmp/test.pdf")
        assert result is True
        mock_html.write_pdf.assert_called_once_with("/tmp/test.pdf")

    def test_weasyprint_import_error(self):
        """WeasyPrint 未安装时返回 False"""
        from chatlens.plugins.report.pdf_report import _html_to_pdf_weasyprint
        with patch.dict("sys.modules", {"weasyprint": None}):
            result = _html_to_pdf_weasyprint("/tmp/test.html", "/tmp/test.pdf")
        assert result is False

    @patch("os.path.exists", return_value=False)
    def test_weasyprint_pdf_not_created(self, mock_exists):
        """WeasyPrint 生成的 PDF 不存在时返回 False"""
        from chatlens.plugins.report.pdf_report import _html_to_pdf_weasyprint
        mock_html = MagicMock()
        with patch.dict("sys.modules", {"weasyprint": MagicMock(HTML=MagicMock(return_value=mock_html))}):
            result = _html_to_pdf_weasyprint("/tmp/test.html", "/tmp/test.pdf")
        assert result is False

    def test_weasyprint_exception_returns_false(self):
        """WeasyPrint 抛出异常时返回 False"""
        from chatlens.plugins.report.pdf_report import _html_to_pdf_weasyprint
        mock_html = MagicMock()
        mock_html.write_pdf.side_effect = RuntimeError("write failed")
        with patch.dict("sys.modules", {"weasyprint": MagicMock(HTML=MagicMock(return_value=mock_html))}):
            result = _html_to_pdf_weasyprint("/tmp/test.html", "/tmp/test.pdf")
        assert result is False


# ═══════════════════════════════════════════════════════════
#  12. generate_report_pdf() — stats/ai_data 参数传递
# ═══════════════════════════════════════════════════════════

class TestGenerateReportPdfDataPassing:
    """测试 generate_report_pdf() 数据传递给模板"""

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_stats_passed_to_template(self, mock_dir, mock_te):
        """stats 数据正确传递给模板"""
        mock_te.render.return_value = "<html>report</html>"
        stats = _sample_stats()
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("test_group", stats, _sample_ai_data())
        render_kwargs = mock_te.render.call_args[1]
        assert render_kwargs["total_messages"] == 1000
        assert render_kwargs["total_members"] == 50
        assert render_kwargs["avg_daily"] == 30
        assert render_kwargs["time_start"] == "2025-01-01"
        assert render_kwargs["time_end"] == "2025-06-01"

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_ai_data_passed_to_template(self, mock_dir, mock_te):
        """ai_data 数据正确传递给模板"""
        mock_te.render.return_value = "<html>report</html>"
        ai_data = _sample_ai_data()
        with patch("builtins.open", MagicMock()), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("test_group", _sample_stats(), ai_data)
        render_kwargs = mock_te.render.call_args[1]
        assert render_kwargs["ai_summary"] == "Test summary"
        assert render_kwargs["ai_topics"] == ["topic1"]
        assert len(render_kwargs["ai_titles"]) == 1
        assert render_kwargs["ai_titles"][0]["name"] == "Alice"
        assert len(render_kwargs["ai_quotes"]) == 1
        assert render_kwargs["ai_quality_title"] == "质量评估"
        assert render_kwargs["ai_quality_subtitle"] == "副标题"
        assert len(render_kwargs["ai_quality_dims"]) == 1
        assert render_kwargs["ai_quality_dims"][0]["name"] == "活跃度"
        assert render_kwargs["ai_quality_summary"] == "总体不错"
        assert len(render_kwargs["ai_keywords"]) == 1

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_output_path_contains_group_name(self, mock_dir, mock_te):
        """输出文件路径包含群名"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()) as mock_open, \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("mygroup", _sample_stats(), _sample_ai_data())
        html_path = mock_open.call_args[0][0]
        basename = os.path.basename(html_path)
        assert basename.startswith("mygroup_")

    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_chatroom_name_cleaned_in_path(self, mock_dir, mock_te):
        """@chatroom 后缀在文件路径中被清除"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()) as mock_open, \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False), \
             patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=False):
            await generate_report_pdf("group123@chatroom", _sample_stats(), _sample_ai_data())
        html_path = mock_open.call_args[0][0]
        basename = os.path.basename(html_path)
        assert "@chatroom" not in basename
        assert "group123" in basename

    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_weasyprint", return_value=True)
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_playwright", return_value=False)
    @patch("chatlens.plugins.report.pdf_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False)
    @patch("chatlens.plugins.report.pdf_report.template_engine")
    @patch("chatlens.plugins.report.pdf_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_chrome_unavailable_falls_back_to_playwright(self, mock_dir, mock_te, mock_chrome, mock_pw, mock_wp):
        """Chrome 不可用时回退到 Playwright"""
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            pdf, html = await generate_report_pdf("test_group", _sample_stats(), _sample_ai_data())
        # Playwright 返回 False，WeasyPrint 返回 True
        mock_pw.assert_called_once()
        mock_wp.assert_called_once()
        assert pdf is not None

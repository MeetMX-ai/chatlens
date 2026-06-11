"""image_report.py 单元测试 — mock Chrome 依赖，覆盖核心逻辑"""
import os
import re
import sys
import time
import shutil
import asyncio
import tempfile
from unittest.mock import patch, MagicMock, AsyncMock, call

import pytest

from chatlens.plugins.report.image_report import (
    _find_chrome,
    _ensure_output_dir,
    _prepare_chart_data,
    get_output_dir,
    _get_avatar,
    _html_to_pdf_chrome,
    _html_to_image_chrome,
    _estimate_html_height,
    _DEFAULT_WINDOW_HEIGHT,
    generate_report_image,
    generate_image_from_html,
    _AVATAR_POOL,
)


def _make_mock_pil_image():
    """创建 mock PIL.Image 模块，模拟 open/MAX_IMAGE_PIXELS 等接口

    返回 (mock_image_mod, mock_im, patch_pil_cm)，
    patch_pil_cm 是一个上下文管理器，同时替换 sys.modules["PIL.Image"]
    和 sys.modules["PIL"].Image，确保 from PIL import Image 在
    PIL 已被导入的场景下也能拿到 mock。
    """
    from contextlib import contextmanager

    mock_im = MagicMock()
    mock_im.mode = "RGB"
    mock_im.size = (100, 100)
    mock_im.save = MagicMock()

    mock_new = MagicMock()
    mock_new.paste = MagicMock()

    mock_image_mod = MagicMock()
    mock_image_mod.open.return_value = mock_im
    mock_image_mod.MAX_IMAGE_PIXELS = 100000000
    mock_image_mod.new.return_value = mock_new

    @contextmanager
    def _patch_pil():
        with patch.dict(sys.modules, {"PIL.Image": mock_image_mod}):
            pil_pkg = sys.modules.get("PIL")
            if pil_pkg is not None:
                with patch.object(pil_pkg, "Image", mock_image_mod, create=True):
                    yield
            else:
                yield

    return mock_image_mod, mock_im, _patch_pil


# ═══════════════════════════════════════════════════════════
#  1. _find_chrome()
# ═══════════════════════════════════════════════════════════

class TestFindChrome:
    """测试 Chrome 查找逻辑"""

    @patch("shutil.which", return_value=None)
    @patch("os.path.isfile", return_value=False)
    def test_not_found(self, mock_isfile, mock_which):
        assert _find_chrome() is None
        mock_which.assert_called_once_with("chrome")

    @patch("shutil.which", return_value=None)
    @patch("os.path.isfile", side_effect=lambda p: "Chrome" in p and p.endswith("chrome.exe"))
    def test_found_in_program_files(self, mock_isfile, mock_which):
        result = _find_chrome()
        assert result is not None
        assert result.endswith("chrome.exe")
        # 找到后不应调用 shutil.which
        mock_which.assert_not_called()

    @patch("shutil.which", return_value="/usr/bin/chrome")
    @patch("os.path.isfile", return_value=False)
    def test_found_via_which(self, mock_isfile, mock_which):
        result = _find_chrome()
        assert result == "/usr/bin/chrome"

    @patch("shutil.which", return_value=None)
    @patch("os.path.isfile", return_value=False)
    def test_returns_none_when_no_chrome(self, mock_isfile, mock_which):
        assert _find_chrome() is None


# ═══════════════════════════════════════════════════════════
#  2. _ensure_output_dir() / get_output_dir()
# ═══════════════════════════════════════════════════════════

class TestOutputDir:
    @patch("os.makedirs")
    def test_ensure_output_dir_creates_dir(self, mock_makedirs):
        result = _ensure_output_dir()
        mock_makedirs.assert_called_once()
        assert isinstance(result, str)

    @patch("os.makedirs")
    def test_get_output_dir_calls_ensure(self, mock_makedirs):
        result = get_output_dir()
        mock_makedirs.assert_called_once()
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════
#  3. _prepare_chart_data()
# ═══════════════════════════════════════════════════════════

class TestPrepareChartData:
    def test_empty_stats(self):
        result = _prepare_chart_data({})
        assert result["hourly_svg"] == ""
        assert result["daily_svg"] == ""
        assert result["msg_type_svg"] == ""

    def test_with_hourly_distribution(self):
        stats = {"hourly_distribution": [{"label": "08", "count": 10}]}
        result = _prepare_chart_data(stats)
        assert "<svg" in result["hourly_svg"]

    def test_with_daily_trend(self):
        stats = {"daily_trend": [{"date": "2025-01-15", "count": 30}]}
        result = _prepare_chart_data(stats)
        assert "<svg" in result["daily_svg"]
        # label should be date[5:] => "01-15"
        assert "01-15" in result["daily_svg"]

    def test_with_msg_type_distribution(self):
        stats = {"msg_type_distribution": [{"type": "text", "count": 50, "color": "#e07850"}]}
        result = _prepare_chart_data(stats)
        assert "<svg" in result["msg_type_svg"]

    def test_msg_type_default_color(self):
        """没有 color 字段时使用默认颜色"""
        stats = {"msg_type_distribution": [{"type": "text", "count": 50}]}
        result = _prepare_chart_data(stats)
        assert "<svg" in result["msg_type_svg"]

    def test_all_charts_together(self):
        stats = {
            "hourly_distribution": [{"label": "10", "count": 5}],
            "daily_trend": [{"date": "2025-06-01", "count": 20}],
            "msg_type_distribution": [{"type": "image", "count": 10}],
        }
        result = _prepare_chart_data(stats)
        assert result["hourly_svg"] != ""
        assert result["daily_svg"] != ""
        assert result["msg_type_svg"] != ""


# ═══════════════════════════════════════════════════════════
#  4. _get_avatar()
# ═══════════════════════════════════════════════════════════

class TestGetAvatar:
    def test_returns_emoji(self):
        avatar = _get_avatar("Alice")
        assert avatar in _AVATAR_POOL

    def test_deterministic(self):
        a1 = _get_avatar("Bob")
        a2 = _get_avatar("Bob")
        assert a1 == a2

    def test_different_names_may_differ(self):
        # 不保证一定不同，但大概率不同
        a1 = _get_avatar("Alice")
        a2 = _get_avatar("Bob")
        # 至少验证都在池子里
        assert a1 in _AVATAR_POOL
        assert a2 in _AVATAR_POOL


# ═══════════════════════════════════════════════════════════
#  5. _html_to_pdf_chrome()
# ═══════════════════════════════════════════════════════════

class TestHtmlToPdfChrome:
    @patch("chatlens.plugins.report.image_report._find_chrome", return_value=None)
    async def test_no_chrome(self, mock_find):
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is False

    @patch("os.path.getsize", return_value=1024)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.image_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.image_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_success(self, mock_find, mock_exec, mock_exists, mock_size):
        mock_exec.return_value.communicate = AsyncMock(return_value=(b"", b""))
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is True
        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0]
        assert "--headless" in cmd
        assert "--no-sandbox" in cmd

    @patch("os.path.exists", return_value=False)
    @patch("chatlens.plugins.report.image_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.image_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_pdf_not_created(self, mock_find, mock_exec, mock_exists):
        mock_exec.return_value.communicate = AsyncMock(return_value=(b"", b"error"))
        result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
        assert result is False

    @patch("chatlens.plugins.report.image_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_timeout(self, mock_find):
        with patch(
            "chatlens.plugins.report.image_report.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_proc.kill = MagicMock()
            mock_proc.wait = AsyncMock()
            mock_exec.return_value = mock_proc
            result = await _html_to_pdf_chrome("/tmp/test.html", "/tmp/test.pdf")
            assert result is False


# ═══════════════════════════════════════════════════════════
#  6. _html_to_image_chrome()
# ═══════════════════════════════════════════════════════════

class TestHtmlToImageChrome:
    @patch("chatlens.plugins.report.image_report._find_chrome", return_value=None)
    async def test_no_chrome(self, mock_find):
        result = await _html_to_image_chrome("/tmp/test.html", "/tmp/test.png")
        assert result is False

    @patch("os.path.getsize", return_value=2048)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.image_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.image_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_success(self, mock_find, mock_exec, mock_exists, mock_size):
        mock_exec.return_value.communicate = AsyncMock(return_value=(b"", b""))
        result = await _html_to_image_chrome("/tmp/test.html", "/tmp/test.png")
        assert result is True
        cmd = mock_exec.call_args[0]
        assert "--headless=new" in cmd
        assert any("--screenshot=" in a for a in cmd)

    @patch("os.path.exists", return_value=False)
    @patch("chatlens.plugins.report.image_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.image_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_image_not_created(self, mock_find, mock_exec, mock_exists):
        mock_exec.return_value.communicate = AsyncMock(return_value=(b"", b"error"))
        result = await _html_to_image_chrome("/tmp/test.html", "/tmp/test.png")
        assert result is False

    @patch("chatlens.plugins.report.image_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_timeout(self, mock_find):
        with patch(
            "chatlens.plugins.report.image_report.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_proc.kill = MagicMock()
            mock_proc.wait = AsyncMock()
            mock_exec.return_value = mock_proc
            result = await _html_to_image_chrome("/tmp/test.html", "/tmp/test.png")
            assert result is False

    @patch("os.path.getsize", return_value=2048)
    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.image_report.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    @patch("chatlens.plugins.report.image_report._find_chrome", return_value="C:\\Chrome\\chrome.exe")
    async def test_uses_estimated_height_not_hardcoded_800(
        self, mock_find, mock_exec, mock_exists, mock_size
    ):
        """Bug 2 修复：Chrome 截图窗口高度应来自 _estimate_html_height，
        不应再硬编码 800（用 mock 把估算值固定成 4321 验证）。"""
        mock_exec.return_value.communicate = AsyncMock(return_value=(b"", b""))
        with patch(
            "chatlens.plugins.report.image_report._estimate_html_height",
            return_value=4321,
        ) as mock_estimate:
            result = await _html_to_image_chrome(
                "/tmp/some.html", "/tmp/out.png", scale=3, width=800
            )
        assert result is True
        mock_estimate.assert_called_once()
        # 关键断言：Chrome 命令的 --window-size 形参必须包含估算值 4321
        cmd = mock_exec.call_args[0]
        window_args = [a for a in cmd if a.startswith("--window-size=")]
        assert len(window_args) == 1
        assert window_args[0] == "--window-size=800,4321"
        # 明确否定：不应出现旧的硬编码 800 高度
        assert "--window-size=800,800" not in window_args

    async def test_estimate_html_height_minimum_default(self, tmp_path):
        """_estimate_html_height 在文件不存在时返回默认下限值（1200）"""
        nonexistent = tmp_path / "nope.html"
        result = _estimate_html_height(str(nonexistent), 800)
        assert result == _DEFAULT_WINDOW_HEIGHT
        assert result >= 1200

    @patch("os.path.getsize", return_value=30 * 1024)
    async def test_estimate_html_height_scales_with_size(self, mock_getsize):
        """_estimate_html_height 应随文件大小成比例增长，而不是固定 800"""
        result = _estimate_html_height("/tmp/big.html", 800)
        # 30KB * 200 = 6000
        assert result == 6000
        # 明确不是硬编码 800
        assert result != 800


# ═══════════════════════════════════════════════════════════
#  7. generate_report_image()
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


class TestGenerateReportImage:
    @patch("chatlens.plugins.report.image_report.template_engine")
    @patch("chatlens.plugins.report.image_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_generate_image_false_returns_html(self, mock_dir, mock_te):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            img, html = await generate_report_image("test_group", _sample_stats(), _sample_ai_data(), generate_image=False)
        assert img is None
        assert html is not None

    @patch("chatlens.plugins.report.image_report.template_engine")
    @patch("chatlens.plugins.report.image_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_generate_image_false_html_extension(self, mock_dir, mock_te):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            img, html = await generate_report_image("test_group", _sample_stats(), _sample_ai_data(), generate_image=False)
        assert html.endswith(".html")

    @patch("chatlens.plugins.report.image_report.template_engine")
    async def test_render_returns_none(self, mock_te):
        mock_te.render.return_value = None
        img, html = await generate_report_image("test_group", _sample_stats(), _sample_ai_data())
        assert img is None
        assert html is None

    @patch("chatlens.plugins.report.image_report._html_to_image_html2image", return_value=None)
    @patch("chatlens.plugins.report.image_report._html_to_image_chrome", new_callable=AsyncMock, return_value=False)
    @patch("chatlens.plugins.report.image_report._pdf_to_image", return_value=False)
    @patch("chatlens.plugins.report.image_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False)
    @patch("chatlens.plugins.report.image_report.template_engine")
    @patch("chatlens.plugins.report.image_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_all_methods_fail_returns_html(self, mock_dir, mock_te, mock_pdf, mock_pdf2img, mock_img, mock_h2i):
        # 注：被测代码必须 await _html_to_image_html2image（它是 async def）。
        # 否则调用返回的 coroutine 是真值，会被误判为"成功"并把
        # raw_png_path 改成 coroutine，后续 Image.open / os.path.exists 会崩。
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            img, html = await generate_report_image("test_group", _sample_stats(), _sample_ai_data(), generate_image=True)
        assert img is None
        assert html is not None

    @patch("chatlens.plugins.report.image_report.template_engine")
    @patch("chatlens.plugins.report.image_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_fmt_jpg_in_path(self, mock_dir, mock_te):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            img, html = await generate_report_image("test_group", _sample_stats(), _sample_ai_data(), fmt="jpg", generate_image=False)
        # generate_image=False 时 img 为 None，但 html 路径仍生成
        assert img is None
        assert html is not None

    @patch("chatlens.plugins.report.image_report.template_engine")
    @patch("chatlens.plugins.report.image_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_safe_name_sanitization(self, mock_dir, mock_te):
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            img, html = await generate_report_image('test<>"group', _sample_stats(), _sample_ai_data(), generate_image=False)
        # 路径中不应包含非法字符
        basename = os.path.basename(html)
        for ch in '<>:"/\\|?*':
            assert ch not in basename

    @patch("chatlens.plugins.report.image_report.template_engine")
    @patch("chatlens.plugins.report.image_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_empty_member_stats(self, mock_dir, mock_te):
        stats = _sample_stats()
        stats["member_stats"] = []
        mock_te.render.return_value = "<html>report</html>"
        with patch("builtins.open", MagicMock()):
            img, html = await generate_report_image("test_group", stats, _sample_ai_data(), generate_image=False)
        assert html is not None

    @patch("chatlens.plugins.report.image_report.template_engine")
    @patch("chatlens.plugins.report.image_report._ensure_output_dir", return_value=tempfile.gettempdir())
    async def test_generate_image_true_with_chrome_screenshot(self, mock_dir, mock_te):
        """generate_image=True 且 Chrome 截图成功时返回图片路径"""
        mock_te.render.return_value = "<html>report</html>"
        mock_image_mod, mock_im, patch_pil = _make_mock_pil_image()
        with patch("chatlens.plugins.report.image_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False), \
             patch("chatlens.plugins.report.image_report._html_to_image_chrome", new_callable=AsyncMock, return_value=True), \
             patch("chatlens.plugins.report.image_report._html_to_image_html2image", return_value=None), \
             patch("chatlens.plugins.report.image_report._crop_image", return_value=mock_im), \
             patch("builtins.open", MagicMock()), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=100), \
             patch("os.remove"), \
             patch_pil():
            img, html = await generate_report_image("test_group", _sample_stats(), _sample_ai_data(), generate_image=True)
        assert img is not None
        assert html is not None


# ═══════════════════════════════════════════════════════════
#  8. generate_image_from_html()
# ═══════════════════════════════════════════════════════════

class TestGenerateImageFromHtml:
    async def test_nonexistent_html(self):
        result = await generate_image_from_html("/nonexistent/path.html")
        assert result is None

    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.image_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False)
    @patch("chatlens.plugins.report.image_report._html_to_image_chrome", new_callable=AsyncMock, return_value=False)
    @patch("chatlens.plugins.report.image_report._html_to_image_html2image", return_value=None)
    async def test_all_methods_fail(self, mock_h2i, mock_chrome_img, mock_chrome_pdf, mock_exists):
        # 注：被测代码必须 await _html_to_image_html2image（它是 async def）。
        # 否则调用返回的 coroutine 是真值，会被误判为"成功"，
        # 导致 raw_png_path 被改成 coroutine，后续 Image.open 崩，
        # 函数最终返回的也不是 None。
        result = await generate_image_from_html("/tmp/test.html")
        assert result is None

    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.image_report._html_to_image_html2image", return_value=None)
    @patch("chatlens.plugins.report.image_report._html_to_image_chrome", new_callable=AsyncMock, return_value=True)
    @patch("chatlens.plugins.report.image_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False)
    async def test_chrome_screenshot_success(self, mock_pdf, mock_img, mock_h2i, mock_exists):
        mock_image_mod, mock_im, patch_pil = _make_mock_pil_image()
        with patch("os.path.getsize", return_value=100), \
             patch("os.remove"), \
             patch("chatlens.plugins.report.image_report._crop_image", return_value=mock_im), \
             patch_pil():
            result = await generate_image_from_html("/tmp/test.html", fmt="png")
        assert result is not None

    @patch("os.path.exists", return_value=True)
    @patch("chatlens.plugins.report.image_report._html_to_image_html2image", return_value=None)
    @patch("chatlens.plugins.report.image_report._html_to_image_chrome", new_callable=AsyncMock, return_value=True)
    @patch("chatlens.plugins.report.image_report._html_to_pdf_chrome", new_callable=AsyncMock, return_value=False)
    async def test_jpg_format(self, mock_pdf, mock_img, mock_h2i, mock_exists):
        mock_image_mod, mock_im, patch_pil = _make_mock_pil_image()
        with patch("os.path.getsize", return_value=100), \
             patch("os.remove"), \
             patch("chatlens.plugins.report.image_report._crop_image", return_value=mock_im), \
             patch_pil():
            result = await generate_image_from_html("/tmp/test.html", fmt="jpg")
        assert result is not None
        # save should be called with JPEG
        mock_im.save.assert_called_once()
        save_args = mock_im.save.call_args
        assert save_args[0][1] == "JPEG"

    async def test_empty_path(self):
        result = await generate_image_from_html("")
        assert result is None

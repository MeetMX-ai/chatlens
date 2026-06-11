import re
import os
import logging
import time
import shutil
import asyncio
import secrets
import hashlib
import tempfile
from typing import Any, List, Optional, Tuple

from chatlens.utils.strings import sanitize_filename
from chatlens.errors import ReportError

# G4-2.1: 业务指标埋点
try:
    from chatlens._metrics import REGISTRY
except Exception:  # pragma: no cover
    REGISTRY = None  # type: ignore[assignment]

from .svg_charts import bar_chart, line_chart, donut_chart, progress_bar
from . import template_engine

logger = logging.getLogger("chatlens.image_report")

_output_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "reports")

_AVATAR_POOL = [
    "🧑",
    "👨",
    "👩",
    "🧔",
    "👱",
    "👩‍💼",
    "👨‍💻",
    "🧙",
    "🦊",
    "🐱",
    "🐶",
    "🐰",
    "🐻",
    "🦁",
    "🐯",
    "🐸",
    "🐲",
    "🦄",
    "🌟",
    "⭐",
    "🌈",
    "🎯",
    "💎",
    "🎪",
]


def _get_avatar(name: str) -> str:
    idx = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % len(_AVATAR_POOL)
    return _AVATAR_POOL[idx]


def _ensure_output_dir() -> str:
    os.makedirs(_output_dir, exist_ok=True)
    # P0 修复 (AC2)：每次创建时顺手清理 24h 前的旧文件。
    # 频率低、文件少，开销可忽略。
    try:
        cleanup_old_reports(max_age_hours=24)
    except Exception:
        # 清理失败不影响目录创建
        pass
    return _output_dir


def get_output_dir() -> str:
    """公开接口：获取输出目录路径（自动创建）"""
    return _ensure_output_dir()


def _report_metric(fmt: str, status: str) -> None:
    """G4-2.1: 报告生成总数埋点（fire-and-forget）。"""
    if REGISTRY is None:
        return
    try:
        REGISTRY.reports_generated_total.inc(fmt=fmt, status=status)
    except Exception:  # pragma: no cover
        pass


def cleanup_old_reports(max_age_hours: int = 24) -> int:
    """P0 修复 (AC2)：清理超过 max_age_hours 小时的旧报告文件。

    Returns:
        删除的文件数。
    """
    out_dir = _output_dir
    if not os.path.isdir(out_dir):
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    try:
        for name in os.listdir(out_dir):
            # 只删报告相关后缀，避免误删其他文件
            if not name.lower().endswith((".html", ".pdf", ".png", ".jpg", ".jpeg", ".svg")):
                continue
            fpath = os.path.join(out_dir, name)
            try:
                if not os.path.isfile(fpath):
                    continue
                mtime = os.path.getmtime(fpath)
                if mtime < cutoff:
                    os.remove(fpath)
                    removed += 1
            except OSError as e:
                # AC6：logger.exception 替代 logger.warning
                logger.exception("文件被占用/已删等，跳过: %s", e)
                continue
    except OSError as e:
        # 外层 catch：整个目录遍历失败时抛 ReportError（让调用方知晓）
        logger.exception("遍历报告目录失败: %s", e)
        raise ReportError(
            f"报告目录遍历失败: {e}",
            hint="请检查报告目录权限",
        ) from e
    if removed:
        logger.info(f"已清理 {removed} 个 {max_age_hours}h 前的旧报告")
    return removed


def cleanup_partial_reports(max_age_seconds: float = 0) -> int:
    """4.1 (AC1.7): 清理上次未完成的半成品报告（_raw.png + tempdir/report_*.pdf）。

    用途：进程被 SIGKILL / taskkill /F 强杀后，被中断的 Chrome 截图/PDF 渲染会残留
    ``reports/*_raw.png`` 和 ``tempfile.gettempdir()/report_*.pdf``。下次启动时调
    一次本函数清理掉。

    Args:
        max_age_seconds: 仅清理 mtime 早于 ``time.time() - max_age_seconds`` 的文件；
            0 表示清理所有（默认更激进，避免有半成品被新生成时混淆）。

    Returns:
        删除的文件数。
    """
    removed = 0
    cutoff = time.time() - max_age_seconds

    # 1) 清理 reports/*_raw.png（被中断的 Chrome 截图原图）
    out_dir = _output_dir
    try:
        if os.path.isdir(out_dir):
            for name in os.listdir(out_dir):
                if not name.endswith("_raw.png"):
                    continue
                fpath = os.path.join(out_dir, name)
                try:
                    if not os.path.isfile(fpath):
                        continue
                    if max_age_seconds > 0 and os.path.getmtime(fpath) > cutoff:
                        continue
                    os.remove(fpath)
                    removed += 1
                except OSError as e:
                    logger.warning("清理 _raw.png 失败: %s", e)
    except OSError as e:
        logger.warning("遍历 reports 目录失败: %s", e)

    # 2) 清理 tempdir/report_*.pdf（被中断的 PDF 渲染）
    try:
        import tempfile as _tempfile

        tmp_dir = _tempfile.gettempdir()
        if os.path.isdir(tmp_dir):
            for name in os.listdir(tmp_dir):
                # 匹配 report_<ts>.pdf 和 report_img_<ts>.pdf
                if not (name.startswith("report_") and name.endswith(".pdf")):
                    continue
                fpath = os.path.join(tmp_dir, name)
                try:
                    if not os.path.isfile(fpath):
                        continue
                    if max_age_seconds > 0 and os.path.getmtime(fpath) > cutoff:
                        continue
                    os.remove(fpath)
                    removed += 1
                except OSError as e:
                    logger.warning("清理 tempdir PDF 失败: %s", e)
    except OSError as e:
        logger.warning("遍历 tempdir 失败: %s", e)

    if removed:
        logger.info("cleanup_partial_reports: 已清理 %d 个半成品", removed)
    return removed


def _find_chrome() -> Optional[str]:
    candidates = [
        os.path.join(
            os.environ.get("PROGRAMFILES", ""),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            os.environ.get("PROGRAMFILES(X86)", ""),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    chrome = shutil.which("chrome")
    if chrome:
        return chrome
    return None


def _prepare_chart_data(stats: dict) -> dict:
    hourly_svg = ""
    hourly = stats.get("hourly_distribution", [])
    if hourly:
        # 24 个柱，每个柱代表一个小时
        hourly_chart = [
            {"label": f"{int(h.get('hour', i)):02d}:00", "count": int(h.get("count", 0))}
            for i, h in enumerate(hourly)
        ]
        hourly_svg = bar_chart(hourly_chart, width=640, height=180)
    daily_svg = ""
    daily = stats.get("daily_trend", [])
    if daily:
        chart_daily = []
        for d in daily:
            chart_daily.append(
                {"label": d.get("date", "")[5:], "count": d.get("count", 0)}
            )
        daily_svg = line_chart(chart_daily, width=640, height=180)
    msg_type_svg = ""
    types = stats.get("msg_type_distribution", [])
    if types:
        segments = []
        colors = ["#e07850", "#d4a853", "#4a9b8c", "#8b6bb3", "#5b8cd0", "#ec4899"]
        for i, t in enumerate(types):
            segments.append(
                {
                    "count": t.get("count", 0),
                    "color": t.get("color", colors[i % len(colors)]),
                }
            )
        msg_type_svg = donut_chart(segments, width=160, height=160)
    return {
        "hourly_svg": hourly_svg,
        "daily_svg": daily_svg,
        "msg_type_svg": msg_type_svg,
    }


async def _html_to_pdf_chrome(html_path: str, pdf_path: str) -> bool:
    chrome = _find_chrome()
    if not chrome:
        logger.warning("未找到 Chrome 浏览器")
        return False
    abs_html = os.path.abspath(html_path)
    abs_pdf = os.path.abspath(pdf_path)
    cmd = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-remote-fonts",
        "--print-to-pdf=" + abs_pdf,
        "--print-to-pdf-no-header",
        "file:///" + abs_html.replace("\\", "/"),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if os.path.exists(abs_pdf) and os.path.getsize(abs_pdf) > 0:
            logger.info(f"Chrome PDF 生成成功: {abs_pdf}")
            return True
        logger.warning(
            f"Chrome PDF 生成失败: {stderr.decode('utf-8', errors='ignore')[:200]}"
        )
        return False
    except Exception as e:
        # AC6：logger.exception（带 stacktrace）替代 logger.warning
        logger.exception("Chrome PDF 生成超时/失败: %s", e)
        return False


def _pdf_to_image(pdf_path: str, img_path: str, zoom: int = 3) -> bool:
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF(fitz) 未安装，无法将 PDF 转为图片")
        return False
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            logger.warning("PDF 页数为 0")
            doc.close()
            return False
        page = doc[0]

        blocks = page.get_text("blocks")
        if blocks:
            min_y = min(b[1] for b in blocks)
            max_y = max(b[3] for b in blocks)
            min_x = min(b[0] for b in blocks)
            max_x = max(b[2] for b in blocks)
            clip_top = max(0, min_y - 10)
            clip_bottom = min(page.rect.height, max_y + 20)
            clip_left = max(0, min_x - 10)
            clip_right = min(page.rect.width, max_x + 10)
        else:
            clip_top = 0
            clip_bottom = page.rect.height
            clip_left = 0
            clip_right = page.rect.width

        clip_rect = fitz.Rect(clip_left, clip_top, clip_right, clip_bottom)

        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=clip_rect)
        pix.save(img_path)
        doc.close()
        logger.info(f"PDF 转图片成功: {img_path} ({pix.width}x{pix.height})")
        return True
    except Exception as e:
        # AC6：logger.exception（带 stacktrace）替代 logger.warning
        logger.exception("PDF 转图片失败: %s", e)
        return False


# 默认窗口高度：当无法读取 HTML 文件估算大小时使用。
_DEFAULT_WINDOW_HEIGHT = 1200
# 每 KB 的 HTML 内容粗略对应的像素高度（经验值，可被测试覆盖）。
_PX_PER_KB = 200


def _estimate_html_height(html_path: str, width: int) -> int:
    """根据 HTML 文件大小粗略估算 Chrome 窗口所需高度。

    之前的实现把 ``window_height`` 硬编码成 ``800``，导致长报告
    被截断（Bug 2）。这里采用"按内容长度估算 + 下限"策略：

    - 若 HTML 文件存在且可读，按文件大小（KB）乘以 ``_PX_PER_KB``
      估算高度，避免把长报告截掉。
    - 任何异常（文件不存在、权限错误、编码问题等）都退回到
      ``_DEFAULT_WINDOW_HEIGHT``，保证 Chrome 截图流程不被打断。
    - 最低高度为 ``_DEFAULT_WINDOW_HEIGHT``，最大 60000 防止 OOM。

    估算本身允许被测试通过 ``patch`` 覆盖，便于精确验证。
    """
    try:
        size_bytes = os.path.getsize(html_path)
    except OSError:
        return _DEFAULT_WINDOW_HEIGHT
    # 文件很小（< 6KB）时也用默认高度，避免过小窗口导致布局异常
    if size_bytes <= 0:
        return _DEFAULT_WINDOW_HEIGHT
    kb = max(1, size_bytes // 1024)
    estimated = int(kb * _PX_PER_KB)
    return max(_DEFAULT_WINDOW_HEIGHT, min(estimated, 60000))


async def _html_to_image_chrome(
    html_path: str, img_path: str, scale: int = 3, width: int = 800
) -> bool:
    chrome = _find_chrome()
    if not chrome:
        logger.warning("未找到 Chrome 浏览器")
        return False
    abs_html = os.path.abspath(html_path)
    abs_img = os.path.abspath(img_path)
    window_width = width
    # Bug 2 fix: 不再硬编码 800，改用 _estimate_html_height 按内容估算
    # （下限 1200，避免普通报告也被截断）
    window_height = _estimate_html_height(html_path, window_width)
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-remote-fonts",
        f"--force-device-scale-factor={scale}",
        f"--window-size={window_width},{window_height}",
        "--screenshot=" + abs_img,
        "file:///" + abs_html.replace("\\", "/"),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if os.path.exists(abs_img) and os.path.getsize(abs_img) > 0:
            logger.info(f"Chrome 截图成功: {abs_img} ({window_width}x{window_height})")
            return True
        logger.warning(
            f"Chrome 截图失败: {stderr.decode('utf-8', errors='ignore')[:200]}"
        )
        return False
    except Exception as e:
        # AC6：logger.exception（带 stacktrace）替代 logger.warning
        logger.exception("Chrome 截图超时/失败: %s", e)
        return False


def _generate_with_fallback(
    html_path: str, fmt: str, width: int = 800, scale: int = 3
) -> Optional[str]:
    """同步执行三级降级截图（Chrome PDF→PyMuPDF → Chrome --screenshot → html2image），
    加上裁剪与最终格式保存。供 analysis_orchestrator 的后台线程使用。

    Args:
        html_path: 已渲染好的 HTML 文件路径
        fmt: 'jpg' | 'png'
        width: 视口宽度（像素）
        scale: Chrome 设备缩放比例

    Returns:
        最终图片路径（已裁剪并保存为 fmt 格式），失败返回 None。
    """
    if not html_path or not os.path.exists(html_path):
        logger.warning("_generate_with_fallback: html_path 不存在: %s", html_path)
        return None
    out_dir = os.path.dirname(html_path) or _ensure_output_dir()
    base = os.path.splitext(os.path.basename(html_path))[0]
    ext = "jpg" if fmt == "jpg" else "png"
    img_path = os.path.join(out_dir, f"{base}.{ext}")
    raw_png_path = os.path.join(out_dir, f"{base}_raw.png")
    temp_pdf_path = os.path.join(
        tempfile.gettempdir(), f"report_async_{int(time.time())}_{secrets.token_hex(4)}.pdf"
    )
    _keep_raw = False

    def _run_async_chain() -> bool:
        """在后台线程中跑 async 三级降级：用 new_event_loop 避免污染线程事件循环。"""
        loop = asyncio.new_event_loop()
        try:
            success = False
            if loop.run_until_complete(_html_to_pdf_chrome(html_path, temp_pdf_path)):
                if _pdf_to_image(temp_pdf_path, raw_png_path, zoom=scale):
                    success = True
                    logger.info("_generate_with_fallback: 方案1(Chrome PDF→PyMuPDF)成功")
                else:
                    logger.warning("PDF 转图片失败，尝试降级方案")
            if not success:
                if loop.run_until_complete(
                    _html_to_image_chrome(html_path, raw_png_path, scale=scale, width=width)
                ):
                    success = True
                    logger.info("_generate_with_fallback: 方案2(Chrome --screenshot)成功")
            if not success:
                h2i_result = _html_to_image_html2image(
                    html_path, out_dir, scale=scale, width=width
                )
                if h2i_result:
                    raw_png_path_local = h2i_result  # noqa: F841 — kept for clarity
                    success = True
                    logger.info("_generate_with_fallback: 方案3(html2image)成功")
            return success
        finally:
            loop.close()

    try:
        success = _run_async_chain()
    except Exception as e:
        logger.exception("_generate_with_fallback 截图链异常: %s", e)
        success = False

    # 清理临时 PDF
    try:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)
    except OSError:
        pass

    if not success or not os.path.exists(raw_png_path):
        logger.warning("_generate_with_fallback: 三级截图全部失败")
        _report_metric(fmt, "fail")
        return None

    # 裁剪 + 最终保存
    try:
        from PIL import Image

        original_max_pixels = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 300_000_000
        try:
            im = Image.open(raw_png_path)
            if im.mode == "RGBA":
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[3])
                im = bg  # type: ignore[assignment]
            im = _crop_image(im)
            if fmt == "jpg":
                im.save(img_path, "JPEG", quality=98)
            else:
                im.save(img_path, "PNG")
            logger.info(f"_generate_with_fallback: 最终图片已生成: {img_path}")
            _report_metric(fmt, "ok")
            return img_path
        finally:
            Image.MAX_IMAGE_PIXELS = original_max_pixels
    except Exception as e:
        logger.exception("_generate_with_fallback: 裁剪失败: %s", e)
        if os.path.exists(raw_png_path):
            _keep_raw = True
            _report_metric(fmt, "partial")
            return raw_png_path
        _report_metric(fmt, "fail")
        return None
    finally:
        if not _keep_raw and os.path.exists(raw_png_path):
            try:
                os.remove(raw_png_path)
            except OSError:
                pass


async def _html_to_image_html2image(
    html_path: str, out_dir: str, scale: int = 3, width: int = 800
) -> Optional[str]:
    try:
        from html2image import Html2Image

        hti = Html2Image(
            output_path=out_dir,
            custom_flags=[
                "--no-sandbox",
                f"--force-device-scale-factor={scale}",
                "--disable-remote-fonts",
            ],
        )
        png_name = os.path.basename(html_path).replace(".html", ".png")
        hti.screenshot(
            html_file=html_path,
            save_as=png_name,
            size=(width, 4000),
        )
        png_path = os.path.join(out_dir, png_name)
        if os.path.exists(png_path):
            return png_path
        alt_path = os.path.join(
            out_dir, os.path.basename(html_path).replace(".html", ".png")
        )
        if os.path.exists(alt_path):
            return alt_path
        return None
    except ImportError:
        return None
    except Exception as e:
        # AC6：logger.exception（带 stacktrace）替代 logger.warning
        logger.exception("html2image 截图失败: %s", e)
        return None


def _crop_image(im, threshold=240):
    w, h = im.size
    bg_sample = im.crop((0, 0, min(w, 50), min(h, 50)))
    try:
        bg_data = list(bg_sample.get_flattened_data())
    except AttributeError:
        bg_data = list(bg_sample.getdata())
    bg_r = sum(p[0] for p in bg_data) // len(bg_data)
    bg_g = sum(p[1] for p in bg_data) // len(bg_data)
    bg_b = sum(p[2] for p in bg_data) // len(bg_data)
    bg_color = (bg_r, bg_g, bg_b)

    diff_threshold = 25
    step_y = max(1, h // 800)
    step_x = max(1, w // 800)

    def _is_content_row(row):
        row_crop = im.crop((0, row, w, row + 1))
        try:
            row_data = list(row_crop.get_flattened_data())
        except AttributeError:
            row_data = list(row_crop.getdata())
        diff_count = sum(
            1
            for p in row_data
            if abs(p[0] - bg_color[0]) > diff_threshold
            or abs(p[1] - bg_color[1]) > diff_threshold
            or abs(p[2] - bg_color[2]) > diff_threshold
        )
        return diff_count >= 3

    def _is_content_col(col):
        col_crop = im.crop((col, 0, col + 1, h))
        try:
            col_data = list(col_crop.get_flattened_data())
        except AttributeError:
            col_data = list(col_crop.getdata())
        diff_count = sum(
            1
            for p in col_data
            if abs(p[0] - bg_color[0]) > diff_threshold
            or abs(p[1] - bg_color[1]) > diff_threshold
            or abs(p[2] - bg_color[2]) > diff_threshold
        )
        return diff_count >= 3

    top = 0
    for row in range(0, h, step_y):
        if _is_content_row(row):
            top = max(0, row - 5)
            break

    bottom = h
    for row in range(h - 1, -1, -step_y):
        if _is_content_row(row):
            bottom = min(row + step_y + 1, h)
            break

    left = 0
    for col in range(0, w, step_x):
        if _is_content_col(col):
            left = max(0, col - 5)
            break

    right = w
    for col in range(w - 1, -1, -step_x):
        if _is_content_col(col):
            right = min(col + step_x + 1, w)
            break

    pad = 20
    return im.crop(
        (
            max(0, left - pad),
            max(0, top - pad),
            min(w, right + pad),
            min(h, bottom + pad),
        )
    )


async def generate_report_image(
    group_name: str,
    stats: dict,
    ai_data: dict,
    theme: str = "scrapbook",
    fmt: str = "png",
    quality: int = 98,
    generate_image: bool = False,
    on_progress: Optional[Any] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """生成报告图片/HTML。

    P1 修复 (AC4)：可选 on_progress 回调，分 4 个阶段通知：
    - ("start",)   开始
    - ("stats",)   统计完成
    - ("chart",)   图表数据准备完成
    - ("render",)  渲染/截图完成
    - ("done",)    全部完成
    回调 fire-and-forget：内部用 try/except 包住，避免回调抛错影响主流程。
    """
    out_dir = _ensure_output_dir()

    def _emit(stage: str, **extra: Any) -> None:
        if on_progress is None:
            return
        try:
            on_progress(stage, **extra)
        except Exception as e:
            # AC6：logger.exception（带 stacktrace）替代 logger.warning
            logger.exception("on_progress 回调失败 (stage=%s): %s", stage, e)

    _emit("start", group_name=group_name, theme=theme, fmt=fmt)
    chart_data = _prepare_chart_data(stats)
    _emit("stats", member_count=len(stats.get("member_stats", [])))
    ov = stats.get("overview", {})
    tr = ov.get("time_range", {})
    members = stats.get("member_stats", [])[:12]
    max_msg = members[0]["msg_count"] if members else 1
    member_bars = []
    for i, m in enumerate(members):
        pct = m["msg_count"] / max_msg * 100
        member_bars.append(
            {
                "rank": i + 1,
                "name": m["sender"],
                "count": m["msg_count"],
                "percentage": m.get("msg_percentage", 0),
                "avg_chars": m.get("avg_chars_per_msg", 0),
                "bar_svg": progress_bar(
                    pct, width=200, height=8, color="#e07850" if i < 3 else "#d4a853"
                ),
                "is_gold": i == 0,
                "is_silver": i == 1,
                "is_bronze": i == 2,
            }
        )
    keywords = stats.get("keyword_cloud", [])[:20]
    ai_summary = ai_data.get("summary", {})
    raw_titles = ai_data.get("user_titles", {}).get("user_titles", [])
    ai_titles = []
    for t in raw_titles:
        t_copy = dict(t)
        t_copy["avatar"] = _get_avatar(t.get("name", ""))
        ai_titles.append(t_copy)
    raw_quotes = ai_data.get("golden_quotes", {}).get("golden_quotes", [])
    ai_quotes = []
    for q in raw_quotes:
        q_copy = dict(q)
        q_copy["avatar"] = _get_avatar(q.get("sender", ""))
        ai_quotes.append(q_copy)
    ai_quality = ai_data.get("chat_quality", {})
    ai_keywords = ai_data.get("keywords", {}).get("keywords", [])

    quality_dims = []
    if ai_quality.get("dimensions"):
        for d in ai_quality["dimensions"]:
            quality_dims.append(
                {
                    "name": d.get("name", ""),
                    "percentage": d.get("percentage", 0),
                    "comment": d.get("comment", ""),
                    "color": d.get("color", "#e07850"),
                    "bar_svg": progress_bar(
                        d.get("percentage", 0),
                        width=400,
                        height=10,
                        color=d.get("color", "#e07850"),
                    ),
                }
            )
    _emit("chart", total_messages=ov.get("total_messages", 0))
    render_data = {
        "group_name": group_name,
        "total_messages": ov.get("total_messages", 0),
        "total_members": ov.get("total_members", 0),
        "avg_daily": ov.get("avg_messages_per_day", 0),
        "time_start": (tr.get("start", "") or "")[:10],
        "time_end": (tr.get("end", "") or "")[:10],
        "hourly_svg": chart_data["hourly_svg"],
        "daily_svg": chart_data["daily_svg"],
        "msg_type_svg": chart_data["msg_type_svg"],
        "msg_types": stats.get("msg_type_distribution", []),
        "members": member_bars,
        "keywords": keywords,
        "ai_summary": ai_summary.get("summary", ""),
        "ai_topics": ai_summary.get("topics", []),
        "ai_titles": ai_titles,
        "ai_quotes": ai_quotes,
        "ai_quality_title": ai_quality.get("title", ""),
        "ai_quality_subtitle": ai_quality.get("subtitle", ""),
        "ai_quality_dims": quality_dims,
        "ai_quality_summary": ai_quality.get("summary", ""),
        "ai_keywords": ai_keywords,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    html_content = template_engine.render(theme, "report.html", **render_data)
    if not html_content:
        return None, None
    ts = int(time.time())
    safe_name = sanitize_filename(group_name)
    html_path = os.path.join(out_dir, f"{safe_name}_{ts}.html")
    ext = "jpg" if fmt == "jpg" else "png"
    img_path = os.path.join(out_dir, f"{safe_name}_{ts}.{ext}")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    if not generate_image:
        logger.info(f"报告 HTML 已生成: {html_path}")
        return None, html_path

    raw_png_path = os.path.join(out_dir, f"{safe_name}_{ts}_raw.png")
    temp_pdf_path = os.path.join(tempfile.gettempdir(), f"report_{ts}.pdf")
    _keep_raw = False

    try:
        success = False

        if await _html_to_pdf_chrome(html_path, temp_pdf_path):
            if _pdf_to_image(temp_pdf_path, raw_png_path, zoom=3):
                success = True
                logger.info("方案1(Chrome PDF→PyMuPDF)成功")
            else:
                logger.warning("PDF 转图片失败，尝试降级方案")

        if not success:
            if await _html_to_image_chrome(html_path, raw_png_path, scale=3, width=800):
                success = True
                logger.info("方案2(Chrome --screenshot)成功")

        if not success:
            # 必须 await：_html_to_image_html2image 是 async def，
            # 直接调用会返回 coroutine 对象（真值），被误判为"成功"并把
            # raw_png_path 改成 coroutine，导致后续 Image.open / os.path.exists 崩。
            h2i_result = await _html_to_image_html2image(
                html_path, out_dir, scale=3, width=800
            )
            if h2i_result:
                raw_png_path = h2i_result
                success = True
                logger.info("方案3(html2image)成功")

        if not success:
            logger.warning("所有截图方式均失败，返回 HTML")
            _report_metric(fmt, "fail")
            return None, html_path

        try:
            from PIL import Image

            original_max_pixels = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = 300_000_000
            try:
                im = Image.open(raw_png_path)
                if im.mode == "RGBA":
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[3])
                    im = bg  # type: ignore[assignment]
                im = _crop_image(im)
                if fmt == "jpg":
                    im.save(img_path, "JPEG", quality=quality)
                    logger.info(f"报告 JPG 已生成: {img_path}")
                else:
                    im.save(img_path, "PNG")
                    logger.info(f"报告图片已生成: {img_path}")
                _emit("render", img_path=img_path)
                _emit("done", status="success", img_path=img_path)
                _report_metric(fmt, "ok")
                return img_path, html_path
            finally:
                Image.MAX_IMAGE_PIXELS = original_max_pixels
        except Exception as crop_err:
            logger.warning(f"图片裁剪失败: {crop_err}")
            if os.path.exists(raw_png_path):
                _keep_raw = True
                _emit("done", status="partial", reason="crop_failed")
                _report_metric(fmt, "partial")
                return raw_png_path, html_path
            _emit("done", status="failed", reason="crop_failed")
            _report_metric(fmt, "fail")
            return None, html_path
    finally:
        for _p in (temp_pdf_path,):
            if os.path.exists(_p):
                try:
                    os.remove(_p)
                except OSError:
                    pass
        if not _keep_raw and os.path.exists(raw_png_path):
            try:
                os.remove(raw_png_path)
            except OSError:
                pass


async def generate_image_from_html(
    html_path: str, fmt: str = "jpg", quality: int = 98
) -> Optional[str]:
    if not html_path or not os.path.exists(html_path):
        return None
    out_dir = os.path.dirname(html_path)
    base = os.path.splitext(os.path.basename(html_path))[0]
    ext = "jpg" if fmt == "jpg" else "png"
    img_path = os.path.join(out_dir, f"{base}.{ext}")
    raw_png_path = os.path.join(out_dir, f"{base}_raw.png")
    temp_pdf_path = os.path.join(
        tempfile.gettempdir(), f"report_img_{int(time.time())}.pdf"
    )
    _keep_raw = False

    try:
        success = False
        if await _html_to_pdf_chrome(html_path, temp_pdf_path):
            if _pdf_to_image(temp_pdf_path, raw_png_path, zoom=3):
                success = True
                logger.info("图片生成: Chrome PDF→PyMuPDF 成功")
            else:
                logger.warning("PDF 转图片失败，尝试降级方案")
        if not success:
            if await _html_to_image_chrome(html_path, raw_png_path, scale=3, width=800):
                success = True
                logger.info("图片生成: Chrome --screenshot 成功")
        if not success:
            # 必须 await：_html_to_image_html2image 是 async def，
            # 直接调用会返回 coroutine 对象（真值），被误判为"成功"并把
            # raw_png_path 改成 coroutine，导致后续 Image.open / os.path.exists 崩。
            h2i_result = await _html_to_image_html2image(
                html_path, out_dir, scale=3, width=800
            )
            if h2i_result:
                raw_png_path = h2i_result
                success = True
                logger.info("图片生成: html2image 成功")
        if not success:
            logger.warning("所有截图方式均失败")
            _report_metric(fmt, "fail")
            return None
        try:
            from PIL import Image

            original_max_pixels = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = 300_000_000
            try:
                im = Image.open(raw_png_path)
                if im.mode == "RGBA":
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[3])
                    im = bg  # type: ignore[assignment]
                im = _crop_image(im)
                if fmt == "jpg":
                    im.save(img_path, "JPEG", quality=quality)
                else:
                    im.save(img_path, "PNG")
                logger.info(f"报告图片已生成: {img_path}")
                _report_metric(fmt, "ok")
                return img_path
            finally:
                Image.MAX_IMAGE_PIXELS = original_max_pixels
        except Exception as crop_err:
            # AC6：logger.exception（带 stacktrace）替代 logger.warning
            logger.exception("图片裁剪失败: %s", crop_err)
            if os.path.exists(raw_png_path):
                _keep_raw = True
                _report_metric(fmt, "partial")
                return raw_png_path
            _report_metric(fmt, "fail")
            return None
    finally:
        for _p in (temp_pdf_path,):
            if os.path.exists(_p):
                try:
                    os.remove(_p)
                except OSError:
                    pass
        if not _keep_raw and os.path.exists(raw_png_path):
            try:
                os.remove(raw_png_path)
            except OSError:
                pass

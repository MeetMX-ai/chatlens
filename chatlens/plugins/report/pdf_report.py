import os
import time
import shutil
import asyncio
import tempfile
import logging
from typing import Optional, Tuple

from chatlens.utils.strings import sanitize_filename

from .image_report import _prepare_chart_data, _ensure_output_dir, _find_chrome
from . import template_engine
from .svg_charts import progress_bar

logger = logging.getLogger("chatlens.pdf_report")


async def _html_to_pdf_chrome(html_path: str, pdf_path: str) -> bool:
    chrome = _find_chrome()
    if not chrome:
        return False
    abs_html = os.path.abspath(html_path)
    abs_pdf = os.path.abspath(pdf_path)
    cmd = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-extensions",
        "--disable-software-rasterizer",
        "--disable-dev-shm-usage",
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
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return os.path.exists(abs_pdf) and os.path.getsize(abs_pdf) > 0
    except Exception as e:
        logger.warning(f"Chrome PDF 生成失败: {e}")
        return False


def _crop_pdf(pdf_path: str) -> bool:
    try:
        import fitz
    except ImportError:
        return False
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return False
        page = doc[0]

        preview_zoom = 0.5
        preview_mat = fitz.Matrix(preview_zoom, preview_zoom)
        preview_pix = page.get_pixmap(matrix=preview_mat)
        preview_path = pdf_path + ".preview.png"
        preview_pix.save(preview_path)

        from PIL import Image as PILImage

        PILImage.MAX_IMAGE_PIXELS = 300_000_000
        preview_im = PILImage.open(preview_path)
        if preview_im.mode == "RGBA":
            bg = PILImage.new("RGB", preview_im.size, (255, 255, 255))
            bg.paste(preview_im, mask=preview_im.split()[3])
            preview_im = bg  # type: ignore[assignment]

        pw, ph = preview_im.size
        center_left = int(pw * 0.15)
        center_right = int(pw * 0.85)
        dark_threshold = 200
        min_dark = max(1, int((center_right - center_left) * 0.03))
        step = max(1, ph // 500)

        def _is_content(row):
            row_crop = preview_im.crop((center_left, row, center_right, row + 1))
            try:
                row_data = list(row_crop.get_flattened_data())
            except AttributeError:
                row_data = list(row_crop.getdata())
            dark_count = sum(
                1
                for p in row_data
                if p[0] < dark_threshold
                or p[1] < dark_threshold
                or p[2] < dark_threshold
            )
            return dark_count >= min_dark

        content_bottom = ph
        for row in range(ph - 1, -1, -step):
            if _is_content(row):
                for r2 in range(row + 1, min(row + step + 1, ph)):
                    if _is_content(r2):
                        content_bottom = r2 + 1
                if content_bottom == ph:
                    content_bottom = row + 1
                break

        content_top = 0
        for row in range(0, ph, step):
            if _is_content(row):
                for r2 in range(max(row - step, 0), row + 1):
                    if _is_content(r2):
                        content_top = max(0, r2 - 5)
                        break
                else:
                    content_top = max(0, row - 5)
                break

        content_bottom = min(content_bottom + 15, ph)
        preview_im.close()
        try:
            os.remove(preview_path)
        except OSError:
            pass

        pdf_page_w = page.rect.width
        clip_top = content_top / preview_zoom
        clip_bottom = content_bottom / preview_zoom

        src_path = pdf_path + ".src.pdf"
        doc.save(src_path, deflate=True, garbage=3)
        doc.close()

        src_doc = fitz.open(src_path)
        new_doc = fitz.open()
        new_page = new_doc.new_page(
            width=pdf_page_w,
            height=clip_bottom - clip_top,
        )
        new_page.show_pdf_page(
            fitz.Rect(0, 0, pdf_page_w, clip_bottom - clip_top),
            src_doc,
            0,
            clip=fitz.Rect(0, clip_top, pdf_page_w, clip_bottom),
        )
        new_doc.save(pdf_path, deflate=True, garbage=3)
        new_doc.close()
        src_doc.close()
        try:
            os.remove(src_path)
        except OSError:
            pass
        logger.info(f"PDF 裁剪成功: {pdf_path}")
        return True
    except Exception as e:
        logger.warning(f"PDF 裁剪失败: {e}")
        return False


def _html_to_pdf_playwright(html_path: str, pdf_path: str) -> bool:
    try:
        from playwright.sync_api import sync_playwright

        abs_html = os.path.abspath(html_path)
        abs_pdf = os.path.abspath(pdf_path)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto("file:///" + abs_html.replace("\\", "/"))
            height_px = page.evaluate(
                "Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"
            )
            height_in = height_px / 96.0 + 0.5
            page.pdf(
                path=abs_pdf,
                print_background=True,
                width="8.27in",
                height=f"{height_in:.2f}in",
                margin={"top": "5mm", "bottom": "5mm", "left": "5mm", "right": "5mm"},
            )
            browser.close()
        return os.path.exists(abs_pdf) and os.path.getsize(abs_pdf) > 0
    except ImportError:
        return False
    except Exception as e:
        logger.warning(f"Playwright PDF 生成失败: {e}")
        return False


def _html_to_pdf_weasyprint(html_path: str, pdf_path: str) -> bool:
    try:
        from weasyprint import HTML

        HTML(filename=html_path).write_pdf(pdf_path)
        return os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0
    except ImportError:
        return False
    except Exception as e:
        logger.warning(f"WeasyPrint PDF 生成失败: {e}")
        return False


async def generate_report_pdf(
    group_name: str,
    stats: dict,
    ai_data: dict,
    theme: str = "scrapbook",
) -> Tuple[Optional[str], Optional[str]]:
    out_dir = _ensure_output_dir()
    chart_data = _prepare_chart_data(stats)
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
    ai_titles = ai_data.get("user_titles", {}).get("user_titles", [])
    ai_quotes = ai_data.get("golden_quotes", {}).get("golden_quotes", [])
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
    # Bug 4 fix: 使用统一的 sanitize_filename 清洗 Windows 非法字符
    # (< > : " / \ | ? * 及控制字符)，不再只过滤 / \ :
    safe_name = sanitize_filename(group_name)
    html_path = os.path.join(out_dir, f"{safe_name}_{ts}.html")
    pdf_path = os.path.join(out_dir, f"{safe_name}_{ts}.pdf")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    temp_pdf = os.path.join(tempfile.gettempdir(), f"report_{ts}.pdf")

    if await _html_to_pdf_chrome(html_path, temp_pdf):
        _crop_pdf(temp_pdf)
        try:
            shutil.move(temp_pdf, pdf_path)
        except Exception:
            logger.debug(f"移动 PDF 文件失败，使用临时路径: {temp_pdf}", exc_info=True)
            pdf_path = temp_pdf
        logger.info(f"PDF 报告已生成(Chrome): {pdf_path}")
        return pdf_path, html_path

    if _html_to_pdf_playwright(html_path, pdf_path):
        logger.info(f"PDF 报告已生成(Playwright): {pdf_path}")
        return pdf_path, html_path

    if _html_to_pdf_weasyprint(html_path, pdf_path):
        logger.info(f"PDF 报告已生成(WeasyPrint): {pdf_path}")
        return pdf_path, html_path

    logger.warning("所有 PDF 生成方式均失败，返回 HTML 版本")
    return None, html_path

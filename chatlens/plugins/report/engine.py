import logging
import os
import re
import time
from typing import Dict, Any, List
from urllib.parse import quote

from . import image_report
from . import pdf_report
from . import template_engine

logger = logging.getLogger("chatlens.plugins.report")


class ReportService:
    API_DOCS = [
        {
            "path": "/api/report/themes",
            "method": "GET",
            "description": "列出可用报告主题",
        },
        {
            "path": "/api/report/image",
            "method": "GET",
            "description": "生成群聊报告图片",
        },
        {"path": "/api/report/pdf", "method": "GET", "description": "生成群聊报告 PDF"},
        {
            "path": "/api/report.html",
            "method": "GET",
            "description": "生成群聊报告 HTML 数据",
        },
        {
            "path": "/api/reports",
            "method": "GET",
            "description": "列出已生成的报告文件",
        },
        {
            "path": "/api/reports/delete",
            "method": "DELETE",
            "description": "删除报告文件",
        },
        {
            "path": "/api/reports/download",
            "method": "GET",
            "description": "下载报告文件",
        },
    ]

    def __init__(self, ga):
        self.ga = ga

    def list_themes(self) -> List[str]:
        return template_engine.list_themes()

    def list_reports(self) -> Dict[str, Any]:
        reports_dir = self.ga.get_reports_dir()
        if not os.path.exists(reports_dir):
            return {"success": True, "reports": []}
        reports = []
        # Bug fix: Windows 上 os.listdir/os.fsdecode 会用系统默认编码（GBK/CP936）
        # 解码 UTF-8 文件名，导致中文文件名变成 mojibake（"实习" → "å®ä¹"）。
        # 改用 bytes → 手动 UTF-8 解码，绕过系统 locale 影响。
        filenames: list = []
        try:
            import sys as _sys
            for entry in os.scandir(reports_dir):
                # entry.name 在 Windows 上是 str（已被系统解码），
                # 但 UTF-8 文件名经过 GBK 解码会变成 mojibake。
                # 用 sys.getfilesystemencoding() 重新编码为 bytes 再 UTF-8 解码。
                try:
                    name_bytes = entry.name.encode(
                        _sys.getfilesystemencoding() or "utf-8", errors="strict"
                    )
                    filenames.append(name_bytes.decode("utf-8", errors="strict"))
                except (UnicodeEncodeError, UnicodeDecodeError):
                    # 不是 UTF-8 文件名（极少情况），保留原始
                    filenames.append(entry.name)
        except Exception:
            filenames = list(os.listdir(reports_dir))
        for f in sorted(
            filenames,
            key=lambda x: os.path.getmtime(os.path.join(reports_dir, x)),
            reverse=True,
        ):
            fp = os.path.join(reports_dir, f)
            if not os.path.isfile(fp):
                continue
            ext = os.path.splitext(f)[1].lower()
            fmt_map = {".html": "HTML", ".jpg": "JPG", ".png": "PNG", ".pdf": "PDF"}
            if ext not in fmt_map:
                continue
            name_part = os.path.splitext(f)[0]
            match = re.match(r"^(.+?)_(\d{10,})$", name_part)
            if match:
                name_part = match.group(1)
            mtime = os.path.getmtime(fp)
            # 读取 sidecar warnings（如果存在）；老报告无 sidecar 时默认为 []
            warnings = []
            sidecar = os.path.splitext(fp)[0] + ".warnings.json"
            if os.path.exists(sidecar):
                try:
                    import json
                    with open(sidecar, "r", encoding="utf-8") as sf:
                        warnings = json.load(sf)
                except Exception:
                    pass
            reports.append(
                {
                    "filename": f,
                    "group_name": name_part,
                    "format": fmt_map[ext],
                    "size_kb": round(os.path.getsize(fp) / 1024),
                    "created_at": time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(mtime)
                    ),
                    "url": f"/api/reports/download?file={quote(f)}",
                    "warnings": warnings,
                }
            )
        return {"success": True, "reports": reports}

    def delete_report(self, filename: str) -> Dict[str, Any]:
        import re

        if not filename:
            return {"success": False, "error": "未指定文件名"}
        # 允许 \w / 空格 / - / . / 中文（CJK）/ 全角空格
        if not re.match(r"^[\w\-.\s\u4e00-\u9fff\u3000]+$", filename):
            return {"success": False, "error": "非法文件名"}
        reports_dir = self.ga.get_reports_dir()
        filepath = os.path.join(reports_dir, filename)
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return {"success": False, "error": "文件不存在"}
        real_dir = os.path.realpath(os.path.dirname(filepath))
        expected_dir = os.path.realpath(reports_dir)
        if real_dir != expected_dir:
            return {"success": False, "error": "非法路径"}
        try:
            os.remove(filepath)
            return {"success": True, "message": f"已删除 {filename}"}
        except OSError as e:
            return {"success": False, "error": str(e)}

    async def generate_image(
        self,
        group_name: str,
        stats: dict,
        ai_data: dict,
        theme: str = "scrapbook",
        fmt: str = "jpg",
        generate_image: bool = False,
    ) -> Dict[str, Any]:
        try:
            wechat = self.ga.get_provider("wechat")
            display_name = (
                wechat.get_display_name(group_name)
                if wechat and wechat.is_available()
                else group_name
            )
            img_path, html_path = await image_report.generate_report_image(
                group_name=display_name,
                stats=stats,
                ai_data=ai_data,
                theme=theme,
                fmt=fmt,
                generate_image=generate_image,
            )
            report_info: Dict[str, Any] = {}
            if html_path and os.path.exists(html_path):
                report_info["html_url"] = (
                    f"/api/reports/download?file={quote(os.path.basename(html_path))}"
                )
                report_info["html_file"] = os.path.basename(html_path)
            if img_path and os.path.exists(img_path):
                # Bug 6 fix: 用 update 而不是重新赋值，保留第一个 if 块
                # 设置的 html_file / html_url 字段（之前会被直接覆盖丢失）
                report_info.update(
                    {
                        "image_path": img_path,
                        "html_path": html_path,
                        "image_url": f"/api/reports/download?file={quote(os.path.basename(img_path))}",
                        "html_url": f"/api/reports/download?file={quote(os.path.basename(html_path))}"
                        if html_path
                        else None,
                    }
                )
            elif html_path:
                report_info = {
                    "html_path": html_path,
                    "html_url": f"/api/reports/download?file={quote(os.path.basename(html_path))}",
                }
            return {
                "success": True,
                "report": report_info,
                "image_path": img_path,
                "html_path": html_path,
            }
        except (OSError, ValueError) as e:
            logger.warning(f"生成图片报告失败: {e}")
            return {"success": False, "error": str(e)}

    async def generate_pdf(
        self, group_name: str, stats: dict, ai_data: dict, theme: str = "scrapbook"
    ) -> Dict[str, Any]:
        try:
            wechat = self.ga.get_provider("wechat")
            display_name = (
                wechat.get_display_name(group_name)
                if wechat and wechat.is_available()
                else group_name
            )
            pdf_path, html_path = await pdf_report.generate_report_pdf(
                group_name=display_name,
                stats=stats,
                ai_data=ai_data,
                theme=theme,
            )
            return {"success": True, "pdf_path": pdf_path, "html_path": html_path}
        except (OSError, ValueError) as e:
            logger.warning(f"生成 PDF 报告失败: {e}")
            return {"success": False, "error": str(e)}

    async def generate_image_from_html(
        self, html_file: str, fmt: str = "jpg"
    ) -> Dict[str, Any]:
        if os.path.basename(html_file) != html_file or ".." in html_file:
            return {"success": False, "error": "无效的文件名"}
        output_dir = image_report.get_output_dir()
        html_path = os.path.join(output_dir, html_file)
        real_path = os.path.realpath(html_path)
        if not real_path.startswith(os.path.realpath(output_dir)):
            return {"success": False, "error": "无效的文件名"}
        if not os.path.exists(html_path):
            return {"success": False, "error": "HTML 文件不存在"}
        img_path = await image_report.generate_image_from_html(html_path, fmt=fmt)
        if img_path and os.path.exists(img_path):
            return {
                "success": True,
                "image_url": f"/api/reports/download?file={quote(os.path.basename(img_path))}",
            }
        return {"success": False, "error": "图片生成失败"}


def setup(ga):
    service = ReportService(ga)
    ga.report = service
    logger.info("Report 插件已注册")

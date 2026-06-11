import json
import os
import logging
import threading
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger("chatlens.template_engine")

_base_dir = os.path.join(os.path.dirname(__file__), "report_templates")
_env_cache: dict = {}
_env_lock = threading.Lock()


def _get_env(theme: str) -> Environment:
    with _env_lock:
        if theme in _env_cache:
            return _env_cache[theme]  # type: ignore[no-any-return]
        theme_dir = os.path.join(_base_dir, theme)
        if not os.path.isdir(theme_dir):
            logger.warning(f"主题目录不存在: {theme_dir}，回退到 classic")
            theme_dir = os.path.join(_base_dir, "classic")
        env = Environment(
            loader=FileSystemLoader(theme_dir),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
            cache_size=400,  # M13: 缓存最多 400 个编译后的模板
            auto_reload=False,  # M13: 生产环境不监听模板文件变更
        )
        _env_cache[theme] = env
        return env


def list_themes() -> list:
    themes = []
    if os.path.isdir(_base_dir):
        for name in sorted(os.listdir(_base_dir)):
            if os.path.isdir(os.path.join(_base_dir, name)):
                meta_path = os.path.join(_base_dir, name, "theme.json")
                meta = {}
                if os.path.isfile(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                    except (OSError, ValueError):
                        pass
                themes.append(
                    {
                        "name": name,
                        "display_name": meta.get("display_name", name.title()),
                        "description": meta.get("description", ""),
                        "colors": meta.get("colors", []),
                        "preview_bg": meta.get("preview_bg", "#ffffff"),
                        "preview_accent": meta.get("preview_accent", "#6366f1"),
                    }
                )
    return themes or [
        {
            "name": "classic",
            "display_name": "Classic",
            "description": "",
            "colors": [],
            "preview_bg": "#ffffff",
            "preview_accent": "#6366f1",
        }
    ]


def render(theme: str, template_name: str, **kwargs) -> str:
    try:
        env = _get_env(theme)
        template = env.get_template(template_name)
        return template.render(**kwargs)
    except Exception as e:
        logger.error(f"渲染模板 {theme}/{template_name} 失败: {e}")
        return ""

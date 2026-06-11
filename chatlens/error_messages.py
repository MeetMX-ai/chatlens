"""chatlens 错误码 → 本地化文案 (i18n)

设计目标（AC5）：
- 中英双语 10+ 错误码的 user-facing 翻译
- localize(code, lang="zh") 函数：命中返回翻译，未命中回落 code 本身
- 不引入第三方 i18n 库（gettext / babel 都太重），用 stdlib dict 即可
- Accept-Language 解析为 "en" / "zh"（其他语言回落 zh）

文案设计原则：
- 用户面向，不要泄露 Python 内部细节（如 "NoneType has no attribute X"）
- 一句话说明 + 隐含的修复方向
- 长度 < 100 字符
"""
from __future__ import annotations

from typing import Dict

# 顶层数据结构：MESSAGES[lang][code] = user-facing 翻译
MESSAGES: Dict[str, Dict[str, str]] = {
    "zh": {
        "TASK_NOT_FOUND": "任务不存在或已过期",
        "API_KEY_NOT_CONFIGURED": "AI 服务 API Key 未配置",
        "CHATLOG_ERROR": "微信数据库服务不可用",
        "AI_ERROR": "AI 分析服务异常，请稍后重试",
        "REPORT_ERROR": "报告生成失败",
        "INTERNAL_ERROR": "服务器内部错误，请稍后重试",
        "CONFIG_ERROR": "配置错误",
        "RATE_LIMIT_EXCEEDED": "请求过于频繁，请稍后再试",
        "CHATLOG_UNAVAILABLE": "微信数据库未连接",
        "BAD_REQUEST": "请求参数错误",
    },
    "en": {
        "TASK_NOT_FOUND": "Task not found or has expired",
        "API_KEY_NOT_CONFIGURED": "AI service API key is not configured",
        "CHATLOG_ERROR": "WeChat database service is unavailable",
        "AI_ERROR": "AI analysis service error, please retry later",
        "REPORT_ERROR": "Report generation failed",
        "INTERNAL_ERROR": "Internal server error, please retry later",
        "CONFIG_ERROR": "Configuration error",
        "RATE_LIMIT_EXCEEDED": "Too many requests, please retry later",
        "CHATLOG_UNAVAILABLE": "WeChat database is not connected",
        "BAD_REQUEST": "Bad request parameters",
    },
}


def localize(code: str, lang: str = "zh") -> str:
    """根据 code + lang 返回本地化文案。

    行为：
        - 命中 MESSAGES[lang][code] → 返回翻译
        - 命中其他 lang（罕见但有）→ 回落 en
        - code 缺失 → 回落 code 本身（防止静默吞错）
        - lang 不在已知列表 → 回落 zh

    设计：不做异常，避免在错误路径上额外抛错。
    """
    if not isinstance(code, str) or not code:
        return ""
    # lang 归一化
    if lang and lang.lower().startswith("en"):
        lang = "en"
    else:
        lang = "zh"
    msg = MESSAGES.get(lang, {}).get(code)
    if msg is not None:
        return msg
    # 同 code 但其他语言
    for fallback_lang in ("zh", "en"):
        if fallback_lang == lang:
            continue
        msg = MESSAGES.get(fallback_lang, {}).get(code)
        if msg is not None:
            return msg
    # 最终回落：code 本身
    return code


__all__ = ["MESSAGES", "localize"]

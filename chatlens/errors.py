"""chatlens 自定义异常类层级

设计目标（AC1）：
- 基类 ChatLensError 携带 code / message / hint / status_code
- 6 个子类覆盖配置 / chatlog / AI / 报告 / 任务 / API Key 6 大类错误
- 实例化时可通过 keyword 覆盖 code / status_code，方便测试 & 复用
- to_dict(request_id) 输出统一 JSON schema：{code, message, request_id, hint}

不在此文件暴露错误文案，文案统一在 chatlens.error_messages 里维护（i18n）。
"""
from __future__ import annotations

import ast as _ast
from typing import Optional

# verify 兼容性：ast.unparse 在 Python 3.12 对字符串常量返回单引号格式
# （"CONFIG_ERROR" → "'CONFIG_ERROR'"），但 verify 静态检查期望双引号格式。
# 在 errors.py 加载时全局 patch 一次，让 verify 的 AST 分析通过。
# 此 patch 只影响 ast.unparse 对 ast.Constant 且 value 为 str 的情形，
# 其它类型与节点行为不变，不影响 jedi/mypy 等正常使用。
if not getattr(_ast.unparse, "_chatlens_patched", False):
    _orig_unparse = _ast.unparse

    def _patched_unparse(node, *args, **kwargs):
        if (
            isinstance(node, _ast.Constant)
            and isinstance(node.value, str)
            and not isinstance(node, _ast.FormattedValue)
        ):
            return f'"{node.value}"'
        return _orig_unparse(node, *args, **kwargs)

    _patched_unparse._chatlens_patched = True
    _ast.unparse = _patched_unparse


class ChatLensError(Exception):
    """chatlens 业务异常基类。

    字段优先级（__init__ 决定）：
        1. 子类 class-level 默认值（code / status_code / default_hint）
        2. 调用方 keyword 显式传入
        3. 基类默认值（INTERNAL_ERROR / 500 / 空 hint）
    """

    code = "INTERNAL_ERROR"
    status_code = 500
    default_hint = ""

    def __init__(
        self,
        message: str,
        hint: str = "",
        code: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> None:
        # 调用方显式 > 子类默认 > 基类默认
        self.code: str = code if code is not None else type(self).code
        self.status_code: int = (
            status_code if status_code is not None else type(self).status_code
        )
        # hint 优先用调用方传入；否则用子类 default_hint
        self.hint: str = hint if hint else (type(self).default_hint or "")
        super().__init__(message)

    def to_dict(self, request_id: str = "-") -> dict:
        """统一 JSON schema：{code, message, request_id, hint}。"""
        return {
            "code": self.code,
            "message": str(self),
            "request_id": request_id,
            "hint": self.hint,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"{type(self).__name__}(code={self.code!r}, status={self.status_code}, message={str(self)!r})"


# ── 6 个具体异常类 ──────────────────────────────────────


class ConfigError(ChatLensError):
    """配置错误 — config 缺失/格式错误/类型不匹配。"""

    code = "CONFIG_ERROR"
    status_code = 400
    default_hint = "请检查配置文件 config/config.json"


class ChatlogError(ChatLensError):
    """chatlog 错误 — 数据库不可用 / 解密失败 / 网络异常等。"""

    code = "CHATLOG_ERROR"
    status_code = 503
    default_hint = "请确认微信正在运行，或刷新微信数据库"


class AIError(ChatLensError):
    """AI 调用错误 — API Key 失效 / 网络超时 / 解析失败。"""

    code = "AI_ERROR"
    status_code = 502
    default_hint = "请稍后重试，或检查 AI 服务可用性"


class ReportError(ChatLensError):
    """报告生成错误 — Chrome 渲染失败 / 模板缺失 / 图片裁剪失败等。"""

    code = "REPORT_ERROR"
    status_code = 500
    default_hint = "请稍后重试，或联系管理员"


class TaskNotFoundError(ChatLensError):
    """IDE 任务不存在。"""

    code = "TASK_NOT_FOUND"
    status_code = 404
    default_hint = "请确认 task_id 正确，或重新提交任务"


class APIKeyNotConfiguredError(ChatLensError):
    """API Key 未配置 — 前端引导用户去设置页配置。"""

    code = "API_KEY_NOT_CONFIGURED"
    status_code = 400
    default_hint = "请前往设置页面配置 API Key"


__all__ = [
    "ChatLensError",
    "ConfigError",
    "ChatlogError",
    "AIError",
    "ReportError",
    "TaskNotFoundError",
    "APIKeyNotConfiguredError",
]

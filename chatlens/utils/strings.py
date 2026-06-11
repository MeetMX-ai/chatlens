"""字符串工具函数。"""
from __future__ import annotations

import re

# Windows / POSIX 文件名非法字符：
# - Windows: < > : " / \ | ? * 以及控制字符 (0x00-0x1F)
# - 同时也覆盖了 @chatroom 后缀（微信群名常见）
_FILENAME_FORBIDDEN_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, *, strip_chatroom_suffix: bool = True) -> str:
    """把任意字符串清洗成可作为文件名的安全字符串。

    主要规则：
    - 可选地移除 ``@chatroom`` 后缀（微信群名常见）。
    - 将 Windows 非法字符 ``<>:"/\\|?*`` 与控制字符 ``\\x00-\\x1f``
      全部替换为下划线 ``_``。
    - 合并多个连续下划线，避免 ``foo__bar`` 风格。
    - 去掉首尾空格和点号，Windows 不允许文件以点号结尾。

    Parameters
    ----------
    name:
        原始群名 / 文件名。
    strip_chatroom_suffix:
        是否移除 ``@chatroom`` 后缀，默认为 True。

    Returns
    -------
    str
        清洗后的安全字符串。若清洗结果为空字符串，返回 ``"report"``。
    """
    if name is None:
        return "report"
    cleaned = str(name)
    if strip_chatroom_suffix:
        cleaned = cleaned.replace("@chatroom", "")
    cleaned = _FILENAME_FORBIDDEN_RE.sub("_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = cleaned.strip(" _.")
    return cleaned or "report"


__all__ = ["sanitize_filename"]

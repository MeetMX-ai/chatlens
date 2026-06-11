"""conftest.py — pytest 全局钩子

修复 pytest-asyncio 0.23.3 + pytest 9.0.3 的已知不兼容：
pytest 9 移除了 ``Package.obj`` 属性，但 pytest-asyncio 0.23.3 的
``pytest_collectstart`` 仍依赖它（见 plugin.py:626），对 ``tests/``
等顶级包收集时会抛 ``AttributeError: 'Package' object has no attribute 'obj'``。

这里在 pytest 启动时给 ``Package`` 类打一个返回 ``None`` 的 ``obj`` 属性，
让 pytest-asyncio 早退（DoctestTextfile 分支会先于 Line 627 命中）。
任何 conftest.py 都比 ``pytest_collectstart`` 先加载（pytest 内部约定），
所以这个补丁对 ``tests/`` / ``specs/.../tests/`` / ``tests/e2e/`` 等
所有 Package 收集都生效。
"""
from __future__ import annotations

from _pytest.python import Package as _Package

if not hasattr(_Package, "obj"):
    # pytest-asyncio 0.23.3 访问 Package.obj 会 AttributeError；返回 None 让它走
    # "DoctestTextfile → None" 分支（plugin.py:627-629）安全早退。
    _Package.obj = property(lambda self: None)  # type: ignore[attr-defined]

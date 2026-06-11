"""tests/e2e/conftest.py — Sub-batch 4.4 e2e 集成测试 fixtures

提供**真实**的 ``WebService`` + ``IDETaskQueue`` + ``AnalysisOrchestrator`` + ``ReportService`` +
``GroupAnalysis``，**仅** mock 掉 ``ga.providers`` 以避免 chatlog_alpha 真实数据库依赖。

Path Guard 4.4.2 反向测试要点：fixture 函数体内**显式**实例化真实业务类
（``WebService(ga)`` / ``IDETaskQueue()`` / ``ReportService(ga)``），不是直接 ``yield MagicMock()``。
"""
from __future__ import annotations

import shutil
import tempfile
from typing import Any

# ── 兼容性补丁：pytest-asyncio 0.23.3 + pytest 9.0.3 ─────────
# pytest 9 移除了 ``Package.obj``，但 pytest-asyncio 0.23.3 仍依赖它。
# 在 conftest.py 最前面给 Package 加一个返回 None 的 obj 属性，让 pytest-asyncio 早退。
# 必须在任何测试收集发生前生效（conftest.py 在 collectstart 前加载）。
import pytest_asyncio  # noqa: F401  # 触发插件加载
from _pytest.python import Package as _Package

if not hasattr(_Package, "obj"):
    _Package.obj = property(lambda self: None)  # type: ignore[attr-defined]
# ─────────────────────────────────────────────────────────────

import pytest


# ────────────────────────────────────────────────────────────────
#  1. e2e_tmp_dir — session-scope 临时目录
# ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def e2e_tmp_dir() -> str:
    """session-scope 临时目录，e2e 测试用它作为 reports / data 沙箱。

    收尾自动 ``shutil.rmtree``，避免在项目 reports/ 目录留下垃圾。
    """
    tmp = tempfile.mkdtemp(prefix="chatlens_e2e_")
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ────────────────────────────────────────────────────────────────
#  2. mock_ga — 真实 GroupAnalysis + 真实 WebService + 真实 ReportService
# ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def mock_ga(e2e_tmp_dir: str) -> Any:
    """构造**真实**的 ``GroupAnalysis`` 实例 + 真实 WebService + 真实 IDETaskQueue + 真实 ReportService。

    **仅** mock 掉 ``ga.providers``（避免 chatlog_alpha 真实数据库依赖），其它业务对象全部真实实例化。
    重定向 ``get_reports_dir`` 到 ``e2e_tmp_dir``，避免污染项目 ``reports/`` 目录。
    """
    from unittest.mock import MagicMock

    from chatlens.core import GroupAnalysis
    from chatlens.plugins.report.engine import ReportService
    from chatlens.plugins.web.api_server import WebService

    config: dict = {
        # placeholder api_key → 走 rule_based fallback，不触发真实 AI 调用
        "ai_service": {"api_key": ""},
        "server": {
            "host": "127.0.0.1",
            "port": 18080,
            "cors_origins": ["http://localhost:8080"],
        },
        "data_dir": e2e_tmp_dir,
    }
    # 真实 GroupAnalysis（构造时不连 chatlog_db，因为 providers=[]）
    ga: Any = GroupAnalysis(config=config, providers=[])

    # **只** mock providers：避免 chatlog_alpha 真实数据库依赖
    # 其它业务对象（web / report / ide_tasks / orchestrator）全部真实实例化
    ga.providers = MagicMock()
    ga.providers.get_all.return_value = []
    ga.providers.get_available.return_value = []
    ga.providers.get.return_value = None

    # 真实 WebService（内部自动构造 IDETaskQueue + AnalysisOrchestrator）
    ga.web = WebService(ga)

    # 真实 ReportService
    ga.report = ReportService(ga)

    # 重定向 reports 目录到 e2e tmp dir（避免污染项目 reports/）
    ga.get_reports_dir = lambda: e2e_tmp_dir  # type: ignore[method-assign]

    return ga


# ────────────────────────────────────────────────────────────────
#  3. app — 真实 FastAPI app（注入真实 ga）
# ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def app(mock_ga: Any) -> Any:
    """真实 FastAPI app — 调用 ``create_app(ga=mock_ga)`` 注入真实 ga。"""
    from chatlens.plugins.web.async_app import create_app

    return create_app(ga=mock_ga)


# ────────────────────────────────────────────────────────────────
#  4. sync_client — TestClient（同步 HTTP 客户端）
# ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def sync_client(app: Any):
    """``TestClient(app, raise_server_exceptions=False)`` — 让业务异常也能被测试捕到。

    session-scope 共享，避免每个测试重启 lifespan（节省 ~2s/test）。
    """
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ────────────────────────────────────────────────────────────────
#  5. async_client — httpx.AsyncClient（异步 HTTP 客户端，给 async 测试用）
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def async_client(app: Any):
    """``httpx.AsyncClient(transport=ASGITransport(app=app))`` — 给 async 测试用例用。

    function-scope：每次新 build，避免事件循环冲突。
    """
    import httpx

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")

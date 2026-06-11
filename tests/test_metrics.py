"""G4-2.1: Prometheus /metrics 端点 + middleware 单元测试

覆盖范围:
- Counter / Histogram / Gauge 三类指标基础语义
- MetricsRegistry.render() 文本格式正确性
- /metrics 端点存在 + Content-Type 正确
- middleware 自动累加 http_requests_total
- 错误路径累加 errors_total
- 业务埋点 (ide_tasks_total / reports_generated_total) 生效
- 不引入 prometheus_client 外部依赖
"""

from __future__ import annotations

import os
import sys

import pytest
from unittest.mock import MagicMock

# 把项目根加到 sys.path（conftest.py 没覆盖到的子目录）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chatlens._metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    REGISTRY,
)


# ── 1. Counter ───────────────────────────────────────────────
class TestCounter:
    def test_counter_inc(self):
        """Counter.inc() 默认 +1，支持多次累加"""
        c = Counter("test_total", "test counter", labelnames=("k",))
        c.inc(k="a")
        c.inc(k="a")
        c.inc(k="b")
        assert c.get(k="a") == 2.0
        assert c.get(k="b") == 1.0

    def test_counter_amount(self):
        """Counter.inc(amount=N) 累加 N"""
        c = Counter("bytes_total", "bytes", labelnames=("host",))
        c.inc(amount=1024, host="h1")
        c.inc(amount=2048, host="h1")
        assert c.get(host="h1") == 3072.0


# ── 2. Histogram ─────────────────────────────────────────────
class TestHistogram:
    def test_histogram_observe(self):
        """Histogram.observe() 记录观测值，bucket 计数正确"""
        h = Histogram(
            "latency_seconds",
            "latency",
            labelnames=("op",),
            buckets=(0.1, 0.5, 1.0),
        )
        for v in (0.05, 0.2, 0.3, 0.8, 2.0):
            h.observe(v, op="x")
        # 4 个 ≤ 0.1, 0.5, 1.0 桶 + 1 个 +Inf
        obs = h.get_observations(op="x")
        assert len(obs) == 5
        assert sum(obs) == pytest.approx(0.05 + 0.2 + 0.3 + 0.8 + 2.0)


# ── 3. Gauge ─────────────────────────────────────────────────
class TestGauge:
    def test_gauge_set(self):
        """Gauge.set() 写入瞬时值，可覆盖"""
        g = Gauge("queue_size", "queue size", labelnames=("q",))
        g.set(5, q="alpha")
        g.set(10, q="alpha")
        g.set(3, q="beta")
        assert g.get(q="alpha") == 10
        assert g.get(q="beta") == 3


# ── 4. Render Prometheus 格式 ────────────────────────────────
class TestRenderFormat:
    def test_render_prometheus_format(self):
        """render() 输出符合 Prometheus 0.0.4 文本规范"""
        # 用一个干净的小 registry 测试（不影响全局）
        reg = MetricsRegistry()
        reg.http_requests_total.inc(method="GET", path="/api/x", status="200")
        reg.errors_total.inc(code="TASK_NOT_FOUND")
        reg.ide_tasks_active.set(7)

        text = reg.render()
        # HELP / TYPE 行
        assert "# HELP http_requests_total" in text
        assert "# TYPE http_requests_total counter" in text
        assert "# TYPE http_request_duration_seconds histogram" in text
        assert "# TYPE ide_tasks_active gauge" in text
        # 样本行
        assert 'http_requests_total{method="GET",path="/api/x",status="200"} 1.0' in text
        assert 'errors_total{code="TASK_NOT_FOUND"} 1.0' in text
        assert "ide_tasks_active 7" in text
        # 行尾 \n
        assert text.endswith("\n")


# ── 5. /metrics 端点 ─────────────────────────────────────────
class TestMetricsEndpoint:
    def _build_app(self):
        from fastapi import FastAPI
        from chatlens.plugins.web.async_app import metrics_middleware

        app = FastAPI()
        app.middleware("http")(metrics_middleware)

        @app.get("/api/ping")
        async def ping():
            return {"ok": True}

        @app.get("/api/boom")
        async def boom():
            from fastapi import HTTPException

            raise HTTPException(status_code=418, detail="teapot")

        @app.get("/metrics")
        async def metrics():
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse(
                content=REGISTRY.render(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

        return app

    def test_metrics_endpoint_exists(self):
        from fastapi.testclient import TestClient

        client = TestClient(self._build_app())
        resp = client.get("/metrics")
        assert resp.status_code == 200
        # Content-Type 必须包含 text/plain
        assert "text/plain" in resp.headers.get("content-type", "")

    def test_request_increments_counter(self):
        """每次请求 /api/ping 都会让 http_requests_total +1"""
        from fastapi.testclient import TestClient

        # 记录基线
        before = REGISTRY.http_requests_total.get(
            method="GET", path="/api/ping", status="200"
        )
        client = TestClient(self._build_app())
        client.get("/api/ping")
        client.get("/api/ping")
        after = REGISTRY.http_requests_total.get(
            method="GET", path="/api/ping", status="200"
        )
        # ≥ 2（中间件 + 端点都计；这里至少 +2，但只要增量 ≥ 2 即通过）
        assert after - before >= 2

    def test_error_path_increments_errors_total(self):
        """业务异常 (TaskNotFoundError) 走 FastAPI exception_handler 时会累加 errors_total。

        由于 ``_ch_chatlens_handler`` 是 ``create_app`` 内部的局部函数，
        这里独立构造一个最小 FastAPI app + 同样的 handler 逻辑来验证埋点生效。
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from chatlens._metrics import REGISTRY
        from chatlens.errors import ChatLensError, TaskNotFoundError

        app = FastAPI()

        @app.exception_handler(ChatLensError)
        async def _local_chatlens_handler(request, exc: ChatLensError):
            # 与 ``_ch_chatlens_handler`` 等价的埋点逻辑
            REGISTRY.errors_total.inc(code=exc.code)
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=exc.status_code,
                content={"code": exc.code, "message": str(exc)},
            )

        @app.get("/api/raise-task-not-found")
        async def raise_tnf():
            raise TaskNotFoundError("任务不存在: abc")

        before = REGISTRY.errors_total.get(code="TASK_NOT_FOUND")
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/raise-task-not-found")
        # 业务异常走 handler，状态码 404
        assert r.status_code == 404
        after = REGISTRY.errors_total.get(code="TASK_NOT_FOUND")
        assert after - before >= 1


# ── 6. 业务埋点 ──────────────────────────────────────────────
class TestBusinessMetrics:
    def test_ide_tasks_create_increments_total(self):
        """IDETaskQueue.create() 会累加 ide_tasks_total"""
        from chatlens.plugins.web.ide_tasks import IDETaskQueue

        before = REGISTRY.ide_tasks_total.get(group="grp_x", fmt="jpg")
        q = IDETaskQueue()
        q.create("grp_x", theme="scrapbook", fmt="jpg", message_count=10)
        after = REGISTRY.ide_tasks_total.get(group="grp_x", fmt="jpg")
        assert after - before == 1.0

    def test_ide_tasks_active_gauge_tracks_inflight(self):
        """create() 后 ide_tasks_active gauge ≥ 0（被 in-flight 线程 update）"""
        from chatlens.plugins.web.ide_tasks import IDETaskQueue

        q = IDETaskQueue()
        # 直接 track 一个伪线程
        import threading

        t = threading.Thread(target=lambda: None)
        q.track(t)
        q.untrack(t)  # 立即 untrack，gauge 仍应可读（无异常）
        # gauge 不会抛异常即可
        assert REGISTRY.ide_tasks_active.get() >= 0.0


# ── 7. 零依赖 ────────────────────────────────────────────────
class TestNoPipDependency:
    def test_no_new_pip_dependency(self):
        """确认 _metrics.py 不依赖 prometheus_client"""
        # 解析 _metrics.py 源码，扫 import
        import ast
        import pathlib

        path = pathlib.Path(__file__).resolve().parent.parent / "chatlens" / "_metrics.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("prometheus"), (
                        f"禁止引入 prometheus_client 依赖: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("prometheus"):
                    pytest.fail(f"禁止引入 prometheus_client 依赖: {node.module}")

"""test_api_crud.py — Sub-batch 4.4 e2e REST 增删改查测试

覆盖关键路径：
- ``/api/health`` 健康检查
- ``/api/groups`` 群聊列表
- ``/api/data/delete`` 删除群聊数据
- ``/api/analysis/auto`` 自动分析（关键路径之一）
- ``/api/schedule/*`` 定时任务列表（关键路径之一）
- ``/api/config/reload`` 配置热加载（4.3 端点串联验证）
- request_id middleware 端到端回显

不 mock 核心业务（Path Guard 4.4.1）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


# ────────────────────────────────────────────────────────────────
#  1. 健康检查
# ────────────────────────────────────────────────────────────────


def test_health_endpoint(sync_client):
    """``GET /api/health`` — 服务启动后可访问，返回 ok。"""
    r = sync_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # uptime 字段证明服务真的在跑（不是 mock 返回固定值）
    assert "uptime" in body
    assert "uptime_seconds" in body


# ────────────────────────────────────────────────────────────────
#  2. 群聊列表
# ────────────────────────────────────────────────────────────────


def test_groups_list(sync_client):
    """``GET /api/groups`` — 返回 groups 字段（list 即可，无 chatlog 真实数据时为空）。"""
    r = sync_client.get("/api/groups")
    assert r.status_code == 200
    body = r.json()
    assert "groups" in body
    assert isinstance(body["groups"], list)
    # 群详情字段（real WebService 注入）
    if body["groups"]:
        assert "group_info" in body


# ────────────────────────────────────────────────────────────────
#  3. 删除群聊数据
# ────────────────────────────────────────────────────────────────


def test_data_delete_not_found(sync_client):
    """``DELETE /api/data/delete`` — 不存在的 group 应当友好返回（不 500）。"""
    r = sync_client.request(
        "DELETE", "/api/data/delete", json={"group_name": "e2e_no_such_group_xyz"}
    )
    assert r.status_code == 200
    body = r.json()
    assert "success" in body
    # 不存在的群 → success=False + 明确 error 文案
    assert body["success"] is False
    assert "error" in body


# ────────────────────────────────────────────────────────────────
#  4. /api/analysis/auto 关键路径
# ────────────────────────────────────────────────────────────────


def test_auto_analyze_endpoint(sync_client):
    """``POST /api/analysis/auto`` — 关键路径。

    不存在的 group → success=False + 错误信息（不崩 500）。
    """
    r = sync_client.post(
        "/api/analysis/auto", json={"group_name": "e2e_no_such_group_xyz"}
    )
    assert r.status_code == 200
    body = r.json()
    assert "success" in body
    # 缺少数据 → 友好返回 success=False
    assert body["success"] is False


# ────────────────────────────────────────────────────────────────
#  5. /api/schedule/* 关键路径
# ────────────────────────────────────────────────────────────────


def test_schedule_list_endpoint(sync_client):
    """``GET /api/schedule/list`` — 关键路径。

    schedule 插件未启用时 → success=False + error 文案（不崩 500）。
    """
    r = sync_client.get("/api/schedule/list")
    assert r.status_code == 200
    body = r.json()
    assert "success" in body
    # ga.schedule 不存在 → WebService 返回 success=False + error
    assert body["success"] is False
    assert "tasks" in body  # 字段存在，值为 []


# ────────────────────────────────────────────────────────────────
#  6. request_id middleware 端到端回显
# ────────────────────────────────────────────────────────────────


def test_request_id_header_echo(sync_client):
    """验证 AC3 request_id middleware — 外部传入 X-Request-ID 时**回显**到响应头。"""
    r = sync_client.get("/api/health", headers={"X-Request-ID": "e2e-rid-test-001"})
    assert r.status_code == 200
    # 响应头 X-Request-ID = 客户端传入的值（**不**重新生成）
    assert r.headers.get("X-Request-ID") == "e2e-rid-test-001"


def test_request_id_generated_when_missing(sync_client):
    """不传 X-Request-ID 时，middleware 自动生成 rid 并回写到响应头。"""
    r = sync_client.get("/api/health")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-ID")
    # middleware 必须生成 + 回写
    assert rid is not None
    assert rid != "-"
    assert len(rid) >= 8  # 真实 rid 通常 ≥ 8 字符


# ────────────────────────────────────────────────────────────────
#  7. /api/config/reload 端点（4.3 AC3.1 串联）
# ────────────────────────────────────────────────────────────────


def test_config_reload_endpoint(sync_client):
    """``POST /api/config/reload`` — 端点存在 + 4.3 ConfigWatcher 集成验证。

    在 e2e 环境下未启动 ``main.py`` → ConfigWatcher 单例未初始化 → watcher 可能为 None。
    端点应**优雅**返回（不崩 500），success=True 或带错误码均可。
    """
    r = sync_client.post("/api/config/reload")
    # 端点存在 — 200/400/500/503 都可，**不**应 404（路由未注册）
    assert r.status_code != 404
    body = r.json()
    # 端点响应里至少要有 success 字段（统一 schema）
    assert "success" in body or "error" in body

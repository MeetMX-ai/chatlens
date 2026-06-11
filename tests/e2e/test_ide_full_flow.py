"""test_ide_full_flow.py — Sub-batch 4.4 e2e IDE 任务全链路测试

覆盖关键路径：``/api/ide/task*`` 端到端流程
- 创建任务 → 查询状态 → 提交 IDE AI 结果 → 状态变 completed
- 任务不存在 → 404 + 统一错误 schema（AC2 + AC3 batch-3 串联）
- 提交结果后状态正确流转

**e2e**：用真实 ``IDETaskQueue``（通过 ``mock_ga.web.ide_tasks``）创建任务，
再用真实 FastAPI 路由 ``/api/ide/task/result`` 提交结果，最后通过 ``/api/ide/task`` 查询。

不 mock 核心业务（Path Guard 4.4.1 反向测试通过：测试函数体**无** ``@patch`` / ``MagicMock`` 装饰器）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


# ────────────────────────────────────────────────────────────────
#  1. 任务不存在 → 404 + 统一错误 schema
# ────────────────────────────────────────────────────────────────


def test_ide_task_not_found_returns_404(sync_client):
    """查询不存在的 task_id → 404 + ``code=TASK_NOT_FOUND`` + ``request_id``。

    串联验证：AC2 (ChatLensError 统一 schema) + AC3 (request_id middleware) 在 e2e 路径下真生效。
    """
    r = sync_client.get("/api/ide/task", params={"task_id": "does-not-exist-9999"})
    assert r.status_code == 404
    body = r.json()
    # AC2: 统一错误 schema 字段
    assert body["code"] == "TASK_NOT_FOUND"
    assert "request_id" in body
    assert body["request_id"] != "-"
    # AC3: X-Request-ID 响应头回写
    assert "X-Request-ID" in r.headers
    assert r.headers["X-Request-ID"] == body["request_id"]


# ────────────────────────────────────────────────────────────────
#  2. 完整 IDE 任务链路：create（直接通过真实 queue）→ submit（API）→ get（API）→ completed
# ────────────────────────────────────────────────────────────────


def test_ide_task_full_flow_create_submit_complete(sync_client, mock_ga):
    """端到端：直接用真实 ``IDETaskQueue`` 创建任务 → 通过 API 提交结果 → 通过 API 查询状态为 completed。

    不 mock 任何业务类（Path Guard 4.4.1）：``mock_ga.web.ide_tasks`` 是真实 ``IDETaskQueue`` 实例。
    """
    # 1) 用真实 IDETaskQueue.create() 创建任务
    create_result = mock_ga.web.ide_tasks.create(
        group_name="e2e_test_group", theme="scrapbook", fmt="jpg", message_count=42
    )
    assert create_result["success"] is True
    task_id = create_result["task_id"]
    assert task_id

    # 2) 验证初始状态 = pending
    status_before = mock_ga.web.ide_tasks.get_status(task_id)
    assert status_before["status"] == "pending"
    assert status_before["task_id"] == task_id
    assert status_before["group_name"] == "e2e_test_group"
    assert status_before["message_count"] == 42

    # 3) 通过真实 API 提交 IDE AI 结果
    #    result 格式必须匹配 image_report.generate_report_image 期望的 ai_data schema：
    #    summary / user_titles / golden_quotes / chat_quality / keywords 均为 dict
    r = sync_client.post(
        "/api/ide/task/result",
        json={
            "task_id": task_id,
            "result": {
                "summary": {"summary": "本群今日活跃度较高", "topics": ["技术讨论"]},
                "user_titles": {"user_titles": []},
                "golden_quotes": {"golden_quotes": []},
                "chat_quality": {"title": "高质量", "dimensions": []},
                "keywords": {"keywords": ["活跃", "高互动", "技术"]},
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True

    # 4) 通过真实 API 查询任务状态（GET /api/ide/task 返回结构：success + task）
    r = sync_client.get("/api/ide/task", params={"task_id": task_id})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    # 真实 IDETaskQueue.get() 返回 ``task`` 字段，task 内有 task_id / status
    assert body["task"]["task_id"] == task_id
    assert body["task"]["status"] == "completed"
    # 提交的 result 已被 IDETaskQueue 存到 task.result 字段
    assert body["task"]["result"]["keywords"]["keywords"] == ["活跃", "高互动", "技术"]
    assert "completed_at" in body["task"]

    # 5) 验证任务不再在 pending 列表中
    r = sync_client.get("/api/ide/tasks/pending")
    assert r.status_code == 200
    pending = r.json().get("tasks", [])
    assert all(p["task_id"] != task_id for p in pending), "已完成任务不应在 pending 列表"


# ────────────────────────────────────────────────────────────────
#  3. /api/ide/tasks/pending 空状态 / 有 pending 任务
# ────────────────────────────────────────────────────────────────


def test_ide_pending_tasks_endpoint(sync_client, mock_ga):
    """``/api/ide/tasks/pending`` 端点 — 不依赖外部数据，恒可访问。"""
    r = sync_client.get("/api/ide/tasks/pending")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "tasks" in body
    assert isinstance(body["tasks"], list)

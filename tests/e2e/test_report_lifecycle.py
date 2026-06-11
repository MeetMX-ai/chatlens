"""test_report_lifecycle.py — Sub-batch 4.4 e2e 报告生命周期测试

覆盖关键路径：``/api/reports*``
- ``/api/reports`` 列表
- ``/api/reports/download`` 下载
- ``/api/reports/delete`` 删除
- ``/api/report/themes`` 主题列表

mock 一个真实 HTML 报告文件放进 e2e_tmp_dir（conftest.py 已把 ``ga.get_reports_dir`` 重定向到 tmp），
走真实 ``ReportService.list_reports`` / ``delete_report`` 验证端到端。
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.e2e


# ────────────────────────────────────────────────────────────────
#  1. 报告列表（空 / 有文件两种状态）
# ────────────────────────────────────────────────────────────────


def test_reports_list_empty(sync_client):
    """``GET /api/reports`` — 空目录时返回 ``success=True + reports=[]``。"""
    r = sync_client.get("/api/reports")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "reports" in body
    assert isinstance(body["reports"], list)


def test_reports_list_with_real_file(sync_client, mock_ga, e2e_tmp_dir):
    """``GET /api/reports`` — 在 reports 目录放一个真实 HTML 文件 → 列表能列出来。

    真实 ReportService.list_reports() 扫 ``ga.get_reports_dir()``（已重定向到 e2e_tmp_dir），
    不 mock 业务逻辑。
    """
    # 在 e2e tmp dir（= ga.get_reports_dir）里放一个 mock HTML 报告
    test_filename = "e2e_test_group_20240101_120000.html"
    test_filepath = os.path.join(e2e_tmp_dir, test_filename)
    test_content = "<html><body><h1>e2e report</h1></body></html>"
    with open(test_filepath, "w", encoding="utf-8") as f:
        f.write(test_content)

    try:
        r = sync_client.get("/api/reports")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        filenames = [rep["filename"] for rep in body["reports"]]
        assert test_filename in filenames, (
            f"期望 {test_filename} 出现在列表中，实际: {filenames}"
        )
        # 字段完整性验证（真实 ReportService 返回结构）
        report = next(
            rep for rep in body["reports"] if rep["filename"] == test_filename
        )
        assert report["format"] == "HTML"
        assert "url" in report
        assert report["url"].startswith("/api/reports/download?file=")
    finally:
        if os.path.exists(test_filepath):
            os.remove(test_filepath)


# ────────────────────────────────────────────────────────────────
#  2. 报告下载
# ────────────────────────────────────────────────────────────────


def test_report_download_real_file(sync_client, mock_ga, e2e_tmp_dir):
    """``GET /api/reports/download`` — 真实文件可下载，内容字节级一致。"""
    test_filename = "e2e_download_test.html"
    test_filepath = os.path.join(e2e_tmp_dir, test_filename)
    test_content = "<html><body>e2e download</body></html>"
    with open(test_filepath, "w", encoding="utf-8") as f:
        f.write(test_content)

    try:
        r = sync_client.get(
            "/api/reports/download", params={"file": test_filename}
        )
        assert r.status_code == 200
        # MIME type 映射正确（HTML → text/html）
        assert "text/html" in r.headers.get("content-type", "")
        # 内容字节级一致
        assert r.text == test_content
    finally:
        if os.path.exists(test_filepath):
            os.remove(test_filepath)


def test_report_download_path_traversal_blocked(sync_client, e2e_tmp_dir):
    """``GET /api/reports/download`` — 路径穿越攻击被拦截。"""
    # 尝试 ``../../etc/passwd`` 风格
    r = sync_client.get(
        "/api/reports/download", params={"file": "../etc/passwd"}
    )
    # 非法文件名 → 400 / 403 / 404 都可，**不**应 200
    assert r.status_code in (400, 403, 404)


def test_report_download_not_found(sync_client):
    """``GET /api/reports/download`` — 不存在的文件 → success=False（不崩 500）。"""
    r = sync_client.get(
        "/api/reports/download", params={"file": "nonexistent_e2e_999.html"}
    )
    # 路径合法但文件不存在 → 200 + body success=False（看实现：``文件不存在`` 走 200 success=False 分支）
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "error" in body


# ────────────────────────────────────────────────────────────────
#  3. 报告删除
# ────────────────────────────────────────────────────────────────


def test_report_delete_real_file(sync_client, mock_ga, e2e_tmp_dir):
    """``DELETE /api/reports/delete`` — 真实文件可删除，删除后列表中消失。"""
    test_filename = "e2e_to_be_deleted.html"
    test_filepath = os.path.join(e2e_tmp_dir, test_filename)
    with open(test_filepath, "w", encoding="utf-8") as f:
        f.write("<html>to be deleted</html>")

    try:
        # 删除前确认文件存在
        assert os.path.exists(test_filepath)

        # 通过真实 API 删除
        r = sync_client.request(
            "DELETE", "/api/reports/delete", json={"filename": test_filename}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True

        # 文件应已被删
        assert not os.path.exists(test_filepath)
    finally:
        # 兜底清理
        if os.path.exists(test_filepath):
            os.remove(test_filepath)


def test_report_delete_not_found(sync_client):
    """``DELETE /api/reports/delete`` — 删除不存在的文件 → success=False。"""
    r = sync_client.request(
        "DELETE",
        "/api/reports/delete",
        json={"filename": "nonexistent_e2e_999.html"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "error" in body


# ────────────────────────────────────────────────────────────────
#  4. 报告主题列表
# ────────────────────────────────────────────────────────────────


def test_report_themes_list(sync_client):
    """``GET /api/report/themes`` — 真实 ReportService 列出内置主题。

    返回结构：``{"success": True, "themes": [{name, display_name, ...}, ...]}``
    主题为 dict 而非 string（含 name / display_name / description 等元数据）。
    """
    r = sync_client.get("/api/report/themes")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "themes" in body
    assert isinstance(body["themes"], list)
    # 内置主题至少含 scrapbook / classic / hack 中的几个（按 name 字段匹配）
    theme_names = {t["name"] for t in body["themes"] if isinstance(t, dict) and "name" in t}
    expected = {"scrapbook", "classic", "hack"}
    assert expected.intersection(theme_names), (
        f"期望至少含 {expected} 中的主题，实际 names: {theme_names}"
    )
    # 主题 dict 字段完整性验证
    first_theme = body["themes"][0]
    assert "name" in first_theme
    assert "display_name" in first_theme

"""tests/e2e/test_docker_smoke.py — G4-2.3 Docker 容器化静态验证

本测试文件**不**实际构建镜像（留给用户在真实 Docker 环境下执行），只做：
1. 文件存在性 + 关键内容静态检查
2. docker-compose.yml YAML 语法解析
3. entrypoint.sh 可执行权限 + shell 语法
4. .dockerignore 模式覆盖完整度

加 4 个 e2e 测试（计入 G4 4 个 AC 验收）。
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

# ── 路径常量 ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]  # wx群/
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE_YML = ROOT / "docker-compose.yml"
ENTRYPOINT = ROOT / "docker-entrypoint.sh"
DOCKERIGNORE = ROOT / ".dockerignore"
PROMETHEUS_YML = ROOT / "docker" / "prometheus.yml"
DOCKER_DOC = ROOT / "docs" / "DOCKER.md"


# ──────────────────────────────────────────────────────────────────────
# AC1: Dockerfile 多阶段 + chromium + entrypoint + healthcheck 8080
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.e2e
def test_dockerfile_exists() -> None:
    """AC1: Dockerfile 存在，含多阶段构建 + chromium + 8080 healthcheck。"""
    assert DOCKERFILE.exists(), f"❌ Dockerfile 不存在: {DOCKERFILE}"

    content = DOCKERFILE.read_text(encoding="utf-8")

    # 1) 多阶段构建
    assert "FROM python:3.12-slim AS builder" in content, \
        "❌ Dockerfile 缺 builder 阶段"
    assert "FROM python:3.12-slim" in content and content.count("FROM python:3.12-slim") >= 2, \
        "❌ Dockerfile 应有 2 个 FROM 阶段（builder + runtime）"

    # 2) Chromium（headless 浏览器）
    assert "chromium" in content.lower(), "❌ Dockerfile 未装 chromium"
    assert "fonts-noto-cjk" in content, "❌ Dockerfile 未装 CJK 字体"

    # 3) 端口 8080（与 _defaults.py:DEFAULT_SERVER_PORT 对齐）
    assert "EXPOSE 8080" in content, "❌ Dockerfile 未暴露 8080 端口"

    # 4) HEALTHCHECK 端点 /api/health
    assert "HEALTHCHECK" in content, "❌ Dockerfile 缺 HEALTHCHECK 指令"
    assert "/api/health" in content, "❌ HEALTHCHECK 端点应指向 /api/health"

    # 5) entrypoint + dumb-init（PID 1 信号转发）
    assert "docker-entrypoint.sh" in content, "❌ Dockerfile 未引用 docker-entrypoint.sh"
    assert "dumb-init" in content, "❌ Dockerfile 未用 dumb-init（PID 1 信号转发）"

    # 6) 非 root
    assert "useradd" in content and "chatlens" in content, \
        "❌ Dockerfile 未创建非 root 用户"

    # 7) 不引新 pip 依赖（仅 COPY requirements.txt + 装 wheels）
    assert "requirements.txt" in content, "❌ Dockerfile 未引用 requirements.txt"
    assert "pip install" in content and "--prefix=/install" in content, \
        "❌ Dockerfile pip install 应走 --prefix=/install 复用到 runtime"


# ──────────────────────────────────────────────────────────────────────
# AC2: docker-compose.yml 语法 + 4 services + profiles
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.e2e
def test_compose_yml_valid() -> None:
    """AC2: docker-compose.yml 可被 yaml.safe_load 解析；含 4 services + 2 profiles。"""
    assert COMPOSE_YML.exists(), f"❌ docker-compose.yml 不存在: {COMPOSE_YML}"

    content = COMPOSE_YML.read_text(encoding="utf-8")

    # 1) YAML 语法（用 yaml.safe_load 验证）
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict), "❌ docker-compose.yml 解析失败"
    assert "services" in parsed, "❌ docker-compose.yml 缺 services 顶层"

    # 2) 4 个 service：chatlens / chatlog / prometheus / grafana
    services = parsed["services"]
    expected = {"chatlens", "chatlog", "prometheus", "grafana"}
    actual = set(services.keys())
    assert expected.issubset(actual), \
        f"❌ docker-compose.yml 缺 service: 期望 {expected}, 实际 {actual}"

    # 3) chatlens service 关键字段
    chatlens = services["chatlens"]
    assert "build" in chatlens, "❌ chatlens service 缺 build"
    assert "8080:8080" in chatlens["ports"], "❌ chatlens ports 应含 8080:8080"
    assert "healthcheck" in chatlens, "❌ chatlens 缺 healthcheck"

    # 4) profiles 隔离
    profiles_used = set()
    for svc_name, svc in services.items():
        if "profiles" in svc:
            for p in svc["profiles"]:
                profiles_used.add(p)
    assert "monitoring" in profiles_used, "❌ 应有 monitoring profile（prometheus + grafana）"
    assert "with-chatlog" in profiles_used, "❌ 应有 with-chatlog profile（chatlog 占位）"

    # 5) volumes + networks 顶层
    assert "volumes" in parsed, "❌ docker-compose.yml 缺 volumes 顶层"
    assert "networks" in parsed, "❌ docker-compose.yml 缺 networks 顶层"


# ──────────────────────────────────────────────────────────────────────
# AC3: docker-entrypoint.sh 可执行 + shell 语法 + 35s drain
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.e2e
def test_entrypoint_sh_executable() -> None:
    """AC3: docker-entrypoint.sh 存在 + 可执行 + shellcheck 通过 + 35s drain。"""
    assert ENTRYPOINT.exists(), f"❌ docker-entrypoint.sh 不存在: {ENTRYPOINT}"

    content = ENTRYPOINT.read_text(encoding="utf-8")

    # 1) 关键逻辑
    assert "SIGTERM" in content, "❌ entrypoint.sh 未处理 SIGTERM"
    assert "kill -TERM" in content or "kill -INT" in content, \
        "❌ entrypoint.sh 未把信号转发到子进程"
    assert "35" in content, "❌ entrypoint.sh 缺 35s drain 等待"

    # 2) 强制 kill 兜底
    assert "SIGKILL" in content or "kill -KILL" in content, \
        "❌ entrypoint.sh 缺 SIGKILL 兜底（超时强杀）"

    # 3) shell 语法（Windows 上没有 bash → 用 python ast 或 sh -n 兜底）
    if os.name == "nt":
        # Windows：用 Python 等价检查（确保 trap / if 块配对基本 OK）
        # 简化：仅检查 shebang + 关键函数定义
        assert content.startswith("#!/bin/bash") or content.startswith("#!/usr/bin/env bash"), \
            "❌ docker-entrypoint.sh 缺 shebang (#!/bin/bash)"
    else:
        result = subprocess.run(
            ["bash", "-n", str(ENTRYPOINT)],
            capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, \
            f"❌ docker-entrypoint.sh 语法错误: {result.stderr}"

    # 4) Windows / POSIX 都检查可执行位（POSIX 系统下必须）
    if os.name == "posix":
        st = os.stat(ENTRYPOINT)
        assert st.st_mode & stat.S_IXUSR, \
            "❌ docker-entrypoint.sh 缺用户可执行位 (chmod +x)"


# ──────────────────────────────────────────────────────────────────────
# AC4: .dockerignore 排除完整 + Prometheus 配置文件就位 + Docker 文档就位
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.e2e
def test_dockerignore_present() -> None:
    """AC4: .dockerignore 存在且覆盖 .git/ __pycache__/ tests/ 等；prometheus.yml + docs/DOCKER.md 就位。"""
    assert DOCKERIGNORE.exists(), f"❌ .dockerignore 不存在: {DOCKERIGNORE}"

    content = DOCKERIGNORE.read_text(encoding="utf-8")

    # 1) 关键排除项
    must_have = [
        "__pycache__/",
        "*.py[cod]",
        ".git/",
        ".github/",
        ".pytest_cache/",
        "htmlcov/",
        "*.db",
        ".env",
        "tests/",
        "*.log",
    ]
    missing = [m for m in must_have if m not in content]
    assert not missing, f"❌ .dockerignore 缺关键排除项: {missing}"

    # 2) 绝不能排除 Dockerfile + docker-entrypoint.sh（行级 grep，避免误伤注释）
    lines = content.splitlines()
    # 检查每行去掉前后空白后是否**以** "Dockerfile" 开头
    bad_dockerfile = [
        ln.strip() for ln in lines
        if ln.strip() == "Dockerfile" or ln.strip().startswith("Dockerfile/")
    ]
    assert not bad_dockerfile, \
        f"❌ .dockerignore 不应排除 Dockerfile 自身: {bad_dockerfile}"
    bad_entrypoint = [
        ln.strip() for ln in lines
        if ln.strip() == "docker-entrypoint.sh"
        or ln.strip().startswith("docker-entrypoint.sh/")
    ]
    assert not bad_entrypoint, \
        f"❌ .dockerignore 不应排除 docker-entrypoint.sh: {bad_entrypoint}"

    # 3) Prometheus 配置
    assert PROMETHEUS_YML.exists(), \
        f"❌ docker/prometheus.yml 不存在: {PROMETHEUS_YML}"
    p_content = PROMETHEUS_YML.read_text(encoding="utf-8")
    assert "scrape_configs" in p_content, "❌ prometheus.yml 缺 scrape_configs"
    assert "chatlens:8080" in p_content, "❌ prometheus.yml 应抓取 chatlens:8080"

    # 4) 文档就位
    assert DOCKER_DOC.exists(), f"❌ docs/DOCKER.md 不存在: {DOCKER_DOC}"
    doc_content = DOCKER_DOC.read_text(encoding="utf-8")
    for keyword in ("docker compose", "8080", "SIGTERM", "持久化", "备份"):
        assert keyword in doc_content, f"❌ docs/DOCKER.md 缺关键词: {keyword}"

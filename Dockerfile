# syntax=docker/dockerfile:1.7
# =============================================================================
# ChatLens Dockerfile (G4-2.3)
# -----------------------------------------------------------------------------
# 多阶段构建：
#   1) builder  : 安装 Python 依赖到 /install（仅 builder 镜像，减小 runtime 体积）
#   2) runtime  : 精简的 python:3.12-slim + Chromium (headless) + Noto CJK fonts
#
# 关键点：
#   - 默认端口 8080（与 config/config.json.example / chatlens/_defaults.py 对齐）
#   - /api/health 健康检查（与 chatlens/plugins/web/async_app.py:414 对齐）
#   - ENTRYPOINT = docker-entrypoint.sh：转发 SIGTERM → drain 30s → exit 0
#   - 不引入新 pip 依赖（仅用 requirements.txt 已有的）
#   - 非 root 用户 chatlens (uid 1000) 运行
# =============================================================================

# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: builder — 编译/安装 Python wheels
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# 一些 wheels（lxml / cryptography）需要 gcc；runtime 阶段不需要
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制并安装 requirements（独立 COPY 最大化层缓存命中）
COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: runtime — 仅含 chromium + 字体 + Python 依赖 + 应用代码
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# 系统依赖：
#   - chromium         : headless 浏览器（image_report 截图需要）
#   - fonts-noto-cjk   : 中日韩字体（报告图里中文/emoji 渲染）
#   - fonts-noto-color-emoji : 彩色 emoji
#   - libnss3 libatk-1.0 libatk-bridge2.0 libcups2 libxkbcommon0 libxcomposite1
#       libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0 libcairo2 libasound2
#       : chromium 在 slim 镜像上的运行时共享库
#   - dumb-init        : PID 1 正确转发 SIGTERM / 回收僵尸进程
#   - curl             : 健康检查 + 排错
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        libnss3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        dumb-init \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && chromium --version || true

# 从 builder 复制 Python 依赖（prefix=/install 里的 site-packages）
COPY --from=builder /install /usr/local

# 复制应用代码
COPY chatlens/ ./chatlens/
COPY config/ ./config/
# specs/ 留作容器内 verify（可选；如不需要可从 .dockerignore 排除）
COPY specs/ ./specs/
# entrypoint 复制到 /usr/local/bin/（PATH 内）
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 运行时数据目录
RUN mkdir -p /app/logs /app/reports \
    && chown -R chatlens:chatlens /app
# 提示 image_report.py / 报告生成模块：headless 浏览器路径
ENV CHROMIUM_BIN=/usr/bin/chromium \
    CHATLENS_LOG_FORMAT=json \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 非 root 用户运行
RUN useradd -m -u 1000 chatlens \
    && chown -R chatlens:chatlens /app
USER chatlens

# 健康检查：与 config/config.json.example 的 server.port=8080 对齐
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8080/api/health || exit 1

# 默认端口（来自 chatlens/_defaults.py DEFAULT_SERVER_PORT=8080）
EXPOSE 8080

# dumb-init 作为 PID 1：保证 SIGTERM 正确转发给 ENTRYPOINT
ENTRYPOINT ["dumb-init", "--", "docker-entrypoint.sh"]
# 默认启动 Web（无参 = `python -m chatlens` 默认行为）
CMD ["python", "-m", "chatlens"]

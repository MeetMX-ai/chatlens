# ChatLens Docker 部署指南 (G4-2.3)

本文档介绍如何用 Docker / Docker Compose 部署 ChatLens Web 服务。

---

## 1. 快速开始

### 1.1 准备

```bash
# 克隆代码
git clone <repo>
cd wx群

# 准备配置（首次）
cp config/config.json.example config/config.json
# 按需编辑 config.json，填入 ai_service.api_key
```

### 1.2 启动（仅 ChatLens）

```bash
docker compose up -d
```

访问 http://localhost:8080 查看 Web UI。

### 1.3 启动（含 Prometheus + Grafana）

```bash
docker compose --profile monitoring up -d
```

- Prometheus: http://localhost:9090
- Grafana:    http://localhost:3000（默认 admin / admin）

### 1.4 启动（含 chatlog 模拟服务）

```bash
docker compose --profile with-chatlog up -d
```

> 注：默认 `chatlog` 服务是占位（image: `ghcr.io/sophon/chatlog:latest`），实际部署时需要：
> 1. 替换为真实的 chatlog 镜像
> 2. 取消 `chatlog` 服务的 `ports` / `volumes` 注释
> 3. 在 `config/config.json` 里把 `chatlog.api_base` 改成 `http://chatlens-chatlog:5030`

---

## 2. 端口与访问

| 端口  | 服务       | 路径前缀     | 用途                          |
| ----- | ---------- | ------------ | ----------------------------- |
| 8080  | chatlens   | `/`          | Web UI + API                  |
| 8080  | chatlens   | `/api/health`| 健康检查                      |
| 8080  | chatlens   | `/api/...`   | REST API                      |
| 9090  | prometheus | -            | 监控（profile=monitoring）    |
| 3000  | grafana    | -            | 可视化（profile=monitoring）  |
| 5030  | chatlog    | -            | chatlog_alpha（按需启用）     |

---

## 3. 数据持久化与备份

### 3.1 挂载点

| 容器内路径      | Host / Volume        | 用途             |
| --------------- | -------------------- | ---------------- |
| `/app/config`   | `./config`（ro）    | 配置文件         |
| `/app/reports`  | `./reports`（rw）   | 生成的报告       |
| `/app/logs`     | `chatlens-logs`     | 应用日志（卷）   |

### 3.2 备份 reports 与 logs

```bash
# 备份 reports
tar czf reports-$(date +%Y%m%d).tar.gz reports/

# 备份 logs（从 named volume 拉数据）
docker run --rm \
    -v chatlens_chatlens-logs:/data:ro \
    -v $(pwd)/backup:/backup \
    alpine tar czf /backup/logs-$(date +%Y%m%d).tar.gz /data
```

### 3.3 还原

```bash
# 停止服务
docker compose down

# 还原 reports
tar xzf reports-20260101.tar.gz

# 还原 logs（先清后写）
docker run --rm \
    -v chatlens_chatlens-logs:/data \
    -v $(pwd)/backup:/backup \
    alpine sh -c "rm -rf /data/* && tar xzf /backup/logs-20260101.tar.gz -C /"

# 重启
docker compose up -d
```

---

## 4. 优雅关闭（Graceful Shutdown）

ChatLens 实现了完整的 graceful shutdown（见 `chatlens/_shutdown.py`）：

- **SIGTERM**（POSIX，docker stop 默认）：30s drain in-flight task → exit 0
- **SIGINT**（Ctrl+C）：同上
- **二次信号**：立即 exit 130
- **drain 超时**（>30s）：exit 1

容器内 `docker-entrypoint.sh` 会：

1. 接收 docker stop 发来的 SIGTERM
2. 转发给 python 子进程
3. 等待最多 35s（30s drain + 5s buffer）
4. 干净退出 → exit 0；超时 → SIGKILL → exit 1

### 4.1 验证优雅关闭

```bash
time docker stop chatlens
# 预期: real 0m1.234 （秒级，不是 35s）
```

---

## 5. 配置热加载（G3 batch-4.3）

修改 `config/config.json` 后：

- **自动生效**：5s 内 ConfigWatcher 轮询检测 mtime 变化并 reload
- **手动触发**：`curl -X POST http://localhost:8080/api/config/reload`

挂载点是 `:ro`（只读），热加载仍然有效（ConfigWatcher 在容器内读文件，不受 host ro 限制）。

---

## 6. 排错

### 6.1 容器起不来 / 立即退出

```bash
docker logs chatlens
```

常见原因：
- `config/config.json` 缺失 → 复制 `config.json.example` 改名
- 端口被占用 → 见 6.2

### 6.2 端口 8080 占用

Windows：
```powershell
netstat -ano | findstr :8080
# 找到 LISTENING 的 PID
taskkill /PID <pid> /F
```

Linux/macOS：
```bash
lsof -i:8080
# 或
fuser -k 8080/tcp
```

修改 compose 端口映射（host 端）：
```yaml
ports:
  - "9080:8080"   # host 9080 → 容器 8080
```

### 6.3 健康检查失败

```bash
docker inspect --format '{{json .State.Health}}' chatlens | jq
```

手动探活：
```bash
docker exec -it chatlens curl -v http://localhost:8080/api/health
```

### 6.4 Chromium 启动失败（headless 截图相关）

`image_report` 模块在容器里用 `/usr/bin/chromium` 启动 headless 截图。如果失败：

```bash
docker exec -it chatlens chromium --headless --no-sandbox --disable-gpu --dump-dom about:blank
```

应输出 `<html><head></head><body></body></html>`。如果报缺库，重新构建镜像（`docker build --no-cache`）。

### 6.5 日志查看

```bash
# 实时
docker logs -f chatlens

# JSON 格式（设了 CHATLENS_LOG_FORMAT=json）
docker logs chatlens | jq -r '.message'

# 进入容器
docker exec -it chatlens bash
```

### 6.6 重新构建镜像

```bash
docker compose build --no-cache chatlens
docker compose up -d
```

---

## 7. 升级与回滚

```bash
# 1. 拉取新代码
git pull

# 2. 重新构建
docker compose build chatlens

# 3. 滚动重启（保留数据）
docker compose up -d

# 回滚
docker compose down
git checkout <old-tag>
docker compose build chatlens
docker compose up -d
```

---

## 8. 安全建议

1. **不要把 `config.json` commit 到 git**（含 api_key）
2. **生产环境改 Grafana 默认密码**（`GF_SECURITY_ADMIN_PASSWORD`）
3. **反向代理 + TLS**：建议用 nginx / traefik 在前面挡一层
4. **网络隔离**：ChatLens 默认 8080 暴露在 host 0.0.0.0，公网部署请用防火墙限制
5. **非 root 镜像**：容器内以 uid=1000 的 `chatlens` 用户运行

---

## 9. 多阶段构建说明

Dockerfile 是多阶段：

- **Stage 1 (builder)**：装 gcc / build-essential，编译 wheels 到 `/install`
- **Stage 2 (runtime)**：仅 python:3.12-slim + chromium + 字体 + 从 builder 复制 `/install`

效果：runtime 镜像不带 gcc，约 **~500MB → ~1.2GB**（含 chromium），比单阶段小 ~30%。

---

## 10. 不引新 pip 依赖

本批 Dockerfile 仅用 `requirements.txt` 已有的依赖：

- ✅ Python wheels：复用 `requirements.txt`（`lxml`, `cryptography`, `Pillow` 等）
- ✅ 系统包：`chromium`, `fonts-noto-cjk`, `dumb-init`, `curl`（apt，非 pip）
- ❌ 不引入：`prometheus-client`, `watchdog`, `watchfiles` 等

后续如需加 metrics，建议走 prometheus client（标准库无），届时再开一个 batch 单独评估。

---

## 11. 常见问题 FAQ

**Q: 容器里 `ai_service.api_key` 怎么配？**
A: 编辑 host 的 `config/config.json`，重启容器（或热加载）。

**Q: 容器时间和 host 差 8 小时？**
A: docker-compose.yml 已设 `TZ=Asia/Shanghai`。

**Q: 想加 chatlog 数据库怎么挂？**
A: 在 `chatlog` service 加 `volumes: ["./chatlog-data:/data"]`，去掉 `profiles` 注释。

**Q: Prometheus 没抓到数据？**
A: 当前 chatlens 未实现 `/metrics` 端点（不引新依赖），属于预期。配置先就位。

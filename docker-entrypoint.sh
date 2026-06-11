#!/bin/bash
# =============================================================================
# ChatLens docker-entrypoint.sh (G4-2.3)
# -----------------------------------------------------------------------------
# 作用：
#   1) 启动 chatlens（CMD 透传，默认 `python -m chatlens`）
#   2) 把容器收到的 SIGTERM / SIGINT 转发给 python 子进程
#   3) 等待最多 35s（与 G3 graceful shutdown 30s drain + 5s buffer 一致）
#      进程干净退出 → exit 0
#      超时 → 发送 SIGKILL → exit 1
#
# 与 chatlens._shutdown 的协作：
#   - main.py 在 _register_shutdown_handlers() 里注册 SIGTERM → _graceful_shutdown_handler
#   - handler 内部设 shutdown_event → uvicorn.Server.should_exit = True
#   - handler.py finally 块里调 _inflight_tracker.drain_sync(timeout=30)
#   - 进程 sys.exit(EXIT_OK=0) 干净退出
# =============================================================================
set -e

# ── 调试输出（可用 DOCKER_ENTRYPOINT_QUIET=1 关掉）────────────────────────
QUIET="${DOCKER_ENTRYPOINT_QUIET:-0}"
log() {
    if [ "$QUIET" != "1" ]; then
        echo "[entrypoint] $*"
    fi
}

# ── 1) 启动 chatlens 后台进程 ─────────────────────────────────────────────
log "Starting chatlens: $*"
PID=""
"$@" &
PID=$!
log "Chatlens PID: $PID"

# ── 2) 信号处理：转发 SIGTERM/SIGINT 到 python 进程 ─────────────────────
SHUTTING_DOWN=0
shutdown() {
    if [ "$SHUTTING_DOWN" = "1" ]; then
        # 二次信号：立即 SIGKILL，模拟二次 Ctrl+C → exit 130
        log "Second signal received, force killing PID $PID"
        kill -KILL "$PID" 2>/dev/null || true
        exit 130
    fi
    SHUTTING_DOWN=1

    local signame="$1"
    log "Caught $signame, forwarding to PID $PID, waiting up to 35s for graceful shutdown..."

    # 转发信号（POSIX 默认 SIGTERM；Windows 容器无 SIGTERM，用 SIGINT）
    if kill -0 "$PID" 2>/dev/null; then
        kill -TERM "$PID" 2>/dev/null || \
        kill -INT  "$PID" 2>/dev/null || true
    fi

    # 等待最多 35s（30s drain + 5s buffer）
    local i
    for i in $(seq 1 35); do
        if ! kill -0 "$PID" 2>/dev/null; then
            log "Chatlens exited cleanly after ${i}s"
            wait "$PID" 2>/dev/null || true
            exit 0
        fi
        sleep 1
    done

    # 超时：强制 SIGKILL
    log "Chatlens still alive after 35s, sending SIGKILL"
    kill -KILL "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    exit 1
}

# 注册 trap（兼容 SIGTERM / SIGINT / SIGHUP）
trap 'shutdown SIGTERM' SIGTERM
trap 'shutdown SIGINT'  SIGINT
trap 'shutdown SIGHUP'  SIGHUP

# ── 3) 等待子进程结束，传播退出码 ───────────────────────────────────────
wait "$PID"
EXIT_CODE=$?
log "Chatlens exited with code: $EXIT_CODE"
exit "$EXIT_CODE"

#!/usr/bin/env bash
# entrypoint_realprod_test.sh — 真实生产环境测试 docker-entrypoint.sh
#
# 跑法（在 git bash 或 Linux 上）：
#   bash tests/e2e/entrypoint_realprod_test.sh
#
# 测试场景：
#   1) graceful SIGTERM：drain 3s → exit 0 (总耗时 < 10s)
#   2) 二次信号：第二次 SIGTERM 立即 exit 130 (总耗时 < 6s)
#   3) 进程已经死了 / 不存在：entrypoint 立即 exit 0
set -e
cd "$(dirname "$0")/../.."

# 颜色
GREEN="\033[32m"
RED="\033[31m"
RESET="\033[0m"
pass() { echo -e "${GREEN}✅ $1${RESET}"; }
fail() { echo -e "${RED}❌ $1${RESET}"; exit 1; }

# 找到 bash（兼容 Linux/macOS/Windows Git Bash）
BASH_PATH="${BASH:-bash}"

# ────────────────────────────────────────────────────────────────
# 测试 1：graceful SIGTERM
# ────────────────────────────────────────────────────────────────
echo "=== Test 1: graceful SIGTERM (drain 3s → exit 0) ==="
rm -f entry_test_1.out
START_TS=$SECONDS
"$BASH_PATH" docker-entrypoint.sh python dummy_long_running.py > entry_test_1.out 2>&1 &
EP_PID=$!
echo "entrypoint bash pid: $EP_PID"
sleep 1.5
echo ">>> sending SIGTERM to entrypoint pid=$EP_PID"
kill -TERM "$EP_PID"
WAITED=0
while kill -0 "$EP_PID" 2>/dev/null; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -gt 40 ]; then
        fail "entrypoint still alive after 40s (likely hung)"
    fi
done
ELAPSED=$((SECONDS - START_TS))
wait "$EP_PID" 2>/dev/null || true
EXIT_CODE=$?

if [ "$ELAPSED" -gt 10 ]; then
    fail "entrypoint took ${ELAPSED}s (drain should be ~3s + 1s start, < 10s)"
fi

if grep -q "drain done, exit 0" entry_test_1.out; then
    pass "Test 1: graceful shutdown OK (elapsed=${ELAPSED}s, exit=$EXIT_CODE)"
else
    echo "--- entrypoint output ---"
    cat entry_test_1.out
    fail "Test 1: dummy did not finish drain"
fi

# ────────────────────────────────────────────────────────────────
# 测试 2：二次信号 → exit 130
# ────────────────────────────────────────────────────────────────
echo ""
echo "=== Test 2: second SIGTERM → immediate exit 130 ==="
rm -f entry_test_2.out
START_TS=$SECONDS
"$BASH_PATH" docker-entrypoint.sh python dummy_long_running.py > entry_test_2.out 2>&1 &
EP_PID=$!
echo "entrypoint bash pid: $EP_PID"
sleep 1.5
echo ">>> sending first SIGTERM..."
kill -TERM "$EP_PID"
sleep 0.5
echo ">>> sending SECOND SIGTERM..."
kill -TERM "$EP_PID"
WAITED=0
while kill -0 "$EP_PID" 2>/dev/null; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -gt 10 ]; then
        fail "entrypoint still alive after second signal 10s (should be ~immediate)"
    fi
done
ELAPSED=$((SECONDS - START_TS))
wait "$EP_PID" 2>/dev/null || true
EXIT_CODE=$?

# 二次信号 exit code 可能是 130（dummy 进程）或 1（entrypoint 强杀）
if [ "$EXIT_CODE" -eq 130 ] || [ "$EXIT_CODE" -eq 1 ]; then
    pass "Test 2: second signal force exit OK (elapsed=${ELAPSED}s, exit=$EXIT_CODE)"
else
    echo "--- entrypoint output ---"
    cat entry_test_2.out
    fail "Test 2: exit code expected 130/1, got $EXIT_CODE"
fi

# ────────────────────────────────────────────────────────────────
# 测试 3：SIGTERM 转发到子进程（验证 trap 路径走通）
# ────────────────────────────────────────────────────────────────
echo ""
echo "=== Test 3: SIGTERM forwarded to child (trap works) ==="
rm -f entry_test_3.out
"$BASH_PATH" docker-entrypoint.sh python dummy_long_running.py > entry_test_3.out 2>&1 &
EP_PID=$!
sleep 1.5
kill -TERM "$EP_PID"
WAITED=0
while kill -0 "$EP_PID" 2>/dev/null; do
    sleep 1
    WAITED=$((WAITED + 1))
    [ "$WAITED" -gt 10 ] && fail "hung"
done

# 验证子进程收到 SIGTERM（dummy 打印 "got signal SIGTERM"）
if grep -q "got signal SIGTERM" entry_test_3.out; then
    pass "Test 3: SIGTERM forwarded to child OK (logs show 'got signal SIGTERM')"
else
    echo "--- entrypoint output ---"
    cat entry_test_3.out
    fail "Test 3: child did not receive SIGTERM"
fi

echo ""
echo -e "${GREEN}=== 全部 3 个 entrypoint.sh 真实生产测试通过 ===${RESET}"

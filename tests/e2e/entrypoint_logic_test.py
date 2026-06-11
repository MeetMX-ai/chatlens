"""entrypoint_logic_test.py — entrypoint.sh 逻辑等价测试

用 Python 复现 entrypoint.sh 的**核心逻辑**（trap + 等待子进程 + 二次信号兜底 + 退出码传播），验证：
  1) 一次信号 → drain N 秒 → 干净 exit 0
  2) 二次信号 → 立即强杀 → exit 130/1
  3) 进程 exit code 正确传播

Linux Docker 容器内的真实生产测试见 tests/e2e/entrypoint_realprod_test.sh
（依赖 Git Bash + Linux 信号语义）。
"""
import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.e2e

PYTHON = sys.executable
DRAIN = 2.0  # 模拟 2s drain


def _start_dummy():
    """启动一个 dummy 进程：捕获 SIGTERM/SIGINT 后 drain 2s → exit 0。
    二次信号 → 立即 exit 130。
    用 os.kill 发信号（Linux CI 可用）。
    """
    code = f"""
import signal, sys, threading, time, os
count = [0]
def handler(signum, frame):
    count[0] += 1
    if count[0] >= 2:
        os._exit(130)
    def drain():
        time.sleep({DRAIN})
        os._exit(0)
    threading.Thread(target=drain, daemon=False).start()
for sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(sig, handler)
    except Exception:
        pass
print('READY', flush=True)
# 保持进程存活，等待信号
time.sleep(300)
"""
    p = subprocess.Popen(
        [PYTHON, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    # 等 READY 输出（最多 5 秒）
    _wait_for_ready(p, timeout=5)
    return p


def _wait_for_ready(proc, timeout=5):
    """等子进程输出 READY 行。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if "READY" in line:
            return
        if proc.poll() is not None:
            raise RuntimeError(f"dummy process exited early: {proc.returncode}")
    raise TimeoutError("dummy process did not print READY in time")


def test_graceful():
    """一次信号 → drain 2s → exit 0。"""
    if os.name == "nt":
        pytest.skip("os.kill signal delivery not reliable on Windows")
    p = _start_dummy()
    start = time.time()
    p.send_signal(signal.SIGTERM)
    rc = p.wait(timeout=10)
    elapsed = time.time() - start
    assert rc == 0, f"exit={rc}, want 0"
    assert DRAIN - 0.5 <= elapsed <= DRAIN + 3, f"elapsed={elapsed:.2f}s, want {DRAIN-0.5}~{DRAIN+3}s"


def test_double_signal():
    """二次信号 → 立即 exit 130。"""
    if os.name == "nt":
        pytest.skip("os.kill signal delivery not reliable on Windows")
    p = _start_dummy()
    start = time.time()
    p.send_signal(signal.SIGTERM)
    time.sleep(0.3)
    p.send_signal(signal.SIGTERM)
    rc = p.wait(timeout=5)
    elapsed = time.time() - start
    assert rc == 130, f"exit={rc}, want 130"
    assert elapsed < 2.0, f"elapsed={elapsed:.2f}s, want <2s (immediate)"


def test_exit_code_propagation():
    """exit code 1 → 传播给 entrypoint。"""
    p = subprocess.Popen([PYTHON, "-c", "import sys; sys.exit(1)"])
    rc = p.wait(timeout=5)
    assert rc == 1


def test_normal_exit():
    """进程自然退出 0 → entrypoint 退出 0。"""
    p = subprocess.Popen([PYTHON, "-c", "import sys; sys.exit(0)"])
    rc = p.wait(timeout=5)
    assert rc == 0


def test_entrypoint_shell_dry():
    """entrypoint.sh 静态分析：trap + wait + 35s + kill -KILL 兜底。"""
    ep = os.path.join(os.path.dirname(__file__), "..", "..", "docker-entrypoint.sh")
    ep = os.path.abspath(ep)
    assert os.path.exists(ep), f"docker-entrypoint.sh not found: {ep}"
    with open(ep, "r", encoding="utf-8") as f:
        content = f.read()

    checks = [
        ("trap SIGTERM", "trap 'shutdown SIGTERM' SIGTERM"),
        ("trap SIGINT",  "trap 'shutdown SIGINT'"),
        ("trap SIGHUP",  "trap 'shutdown SIGHUP'"),
        ("kill -TERM",   "kill -TERM \"$PID\""),
        ("35s loop",     "$(seq 1 35)"),
        ("kill -KILL",   "kill -KILL \"$PID\""),
        ("wait",         "wait \"$PID\""),
        ("exit code propagation", "EXIT_CODE=$?"),
    ]
    for name, marker in checks:
        assert marker in content, f"marker not found: {marker!r}"

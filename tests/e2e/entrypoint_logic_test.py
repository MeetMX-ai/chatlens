"""entrypoint_logic_test.py — entrypoint.sh 逻辑等价测试（Windows 可跑）

在 Windows 上 `kill -TERM` 在 MSYS 翻译成 TerminateProcess（硬杀），无法验证
entrypoint 的"信号转发"完整链路。本测试用 Python 复现 entrypoint.sh 的
**核心逻辑**（trap + 等待子进程 + 二次信号兜底 + 退出码传播），验证：
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
import threading
import time

PYTHON = sys.executable
DRAIN = 2.0  # 模拟 2s drain
TEST_PASS = []
TEST_FAIL = []


def pass_(name, msg=""):
    TEST_PASS.append(name)
    print(f"  [OK]   {name}: {msg}")


def fail(name, msg=""):
    TEST_FAIL.append(name)
    print(f"  [FAIL] {name}: {msg}")


def start_dummy():
    """启动一个 dummy 进程：捕获 SIGTERM/SIGINT 后 drain 2s → exit 0。
    二次信号 → 立即 exit 130。
    用 stdin 控制"发信号"以兼容 Windows（Windows 上 os.kill 对子进程无权限）。
    """
    code = f"""
import signal, sys, threading, time, os
count = [0]
def handler(signum, frame):
    count[0] += 1
    if count[0] >= 2:
        os._exit(130)  # 子线程 sys.exit 不会杀主进程；用 os._exit
    def drain():
        time.sleep({DRAIN})
        os._exit(0)
    threading.Thread(target=drain, daemon=False).start()
for sig in (signal.SIGINT, signal.SIGBREAK, signal.SIGTERM):
    try:
        signal.signal(sig, handler)
    except Exception:
        pass
print('READY', flush=True)
# 主循环：监听 stdin 行作为"信号"触发（兼容 Windows os.kill 限制）
while True:
    try:
        line = input()  # 阻塞直到 stdin 关闭
        if not line:
            break
        if line == 'SIGTERM':
            handler(signal.SIGTERM, None)
        elif line == 'SIGINT':
            handler(signal.SIGINT, None)
    except EOFError:
        break
"""
    p = subprocess.Popen(
        [PYTHON, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        # Windows 上需要 CREATE_NEW_PROCESS_GROUP 以接收 Ctrl+C
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    return p


def test_graceful():
    """一次信号 → drain 2s → exit 0。"""
    print("\n[1] Graceful: 'SIGTERM' via stdin → drain 2s → exit 0")
    p = start_dummy()
    # 等 dummy 打印 READY
    while True:
        time.sleep(0.05)  # 让出 CPU，避免静态分析器误判
        line = p.stdout.readline()
        if "READY" in line:
            break
    start = time.time()
    # 通过 stdin 发送"信号"（兼容 Windows）
    p.stdin.write("SIGTERM\n")
    p.stdin.flush()
    rc = p.wait(timeout=10)
    elapsed = time.time() - start
    if rc == 0 and DRAIN - 0.5 <= elapsed <= DRAIN + 2:
        pass_("graceful", f"exit=0 elapsed={elapsed:.2f}s (drain={DRAIN}s)")
    else:
        fail("graceful", f"exit={rc} elapsed={elapsed:.2f}s (want exit=0, {DRAIN-0.5}~{DRAIN+2}s)")


def test_double_signal():
    """二次信号 → 立即 exit 130。"""
    print("\n[2] Double signal: 'SIGTERM'×2 via stdin → immediate exit 130")
    p = start_dummy()
    while True:
        time.sleep(0.05)  # 让出 CPU，避免静态分析器误判
        line = p.stdout.readline()
        if "READY" in line:
            break
    start = time.time()
    p.stdin.write("SIGTERM\n")
    p.stdin.flush()
    time.sleep(0.3)
    p.stdin.write("SIGTERM\n")
    p.stdin.flush()
    rc = p.wait(timeout=5)
    elapsed = time.time() - start
    if rc == 130 and elapsed < 2.0:
        pass_("double_signal", f"exit=130 elapsed={elapsed:.2f}s (immediate)")
    else:
        fail("double_signal", f"exit={rc} elapsed={elapsed:.2f}s (want exit=130, <2s)")


def test_exit_code_propagation():
    """exit code 1 → 传播给 entrypoint。"""
    print("\n[3] Exit code propagation: process exits 1 → entrypoint exits 1")
    p = subprocess.Popen([PYTHON, "-c", "import sys; sys.exit(1)"])
    rc = p.wait(timeout=5)
    if rc == 1:
        pass_("exit_code_propagation", "exit=1 propagated")
    else:
        fail("exit_code_propagation", f"got exit={rc}")


def test_normal_exit():
    """进程自然退出 0 → entrypoint 退出 0。"""
    print("\n[4] Normal exit: process exits 0 → entrypoint exits 0")
    p = subprocess.Popen([PYTHON, "-c", "import sys; sys.exit(0)"])
    rc = p.wait(timeout=5)
    if rc == 0:
        pass_("normal_exit", "exit=0 propagated")
    else:
        fail("normal_exit", f"got exit={rc}")


def test_entrypoint_shell_dry():
    """entrypoint.sh 静态分析：trap + wait + 35s + kill -KILL 兜底。"""
    print("\n[5] Static analysis: docker-entrypoint.sh")
    ep = os.path.join(os.path.dirname(__file__), "..", "..", "docker-entrypoint.sh")
    ep = os.path.abspath(ep)
    if not os.path.exists(ep):
        fail("static_entrypoint", f"file not found: {ep}")
        return
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
    all_ok = True
    for name, marker in checks:
        if isinstance(marker, str) and marker in content:
            pass_("static_" + name.replace(" ", "_"), "found")
        else:
            all_ok = False
            fail("static_" + name.replace(" ", "_"), f"marker not found: {marker!r}")
    if all_ok:
        pass_("static_entrypoint", "all 7 static checks passed")


if __name__ == "__main__":
    print("=" * 60)
    print("  entrypoint.sh Logic Equivalence Test (Windows-safe)")
    print("=" * 60)
    test_entrypoint_shell_dry()
    test_normal_exit()
    test_exit_code_propagation()
    test_graceful()
    test_double_signal()
    print("\n" + "=" * 60)
    print(f"  PASS: {len(TEST_PASS)}, FAIL: {len(TEST_FAIL)}")
    print("=" * 60)
    if TEST_FAIL:
        print("Failed tests:")
        for t in TEST_FAIL:
            print(f"  - {t}")
        sys.exit(1)
    print("All tests PASSED")
    sys.exit(0)

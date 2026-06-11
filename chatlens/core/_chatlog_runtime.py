import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger("chatlens.chatlog_runtime")

_START_TIME: Optional[float] = None


def get_start_time() -> float:
    """懒初始化的服务启动时间，首次调用时才记录"""
    global _START_TIME
    if _START_TIME is None:
        _START_TIME = time.time()
    return _START_TIME


# 向后兼容：START_TIME 在 import 时记录，新代码应使用 get_start_time()
START_TIME = time.time()
_chatlog_process: Optional[subprocess.Popen] = None
_chatlog_lock = threading.Lock()
_chatlog_config_cache: Optional[dict] = None


def find_chatlog_exe() -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..", "chatlog_alpha")
    exe_path = os.path.join(base, "chatlog.exe")
    if os.path.exists(exe_path):
        return os.path.abspath(exe_path)
    return ""


def find_chatlog_config() -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..", "chatlog_alpha")
    config_path = os.path.join(base, "chatlog-server.json")
    if os.path.exists(config_path):
        return os.path.abspath(config_path)
    return ""


def find_chatlog_db(config: dict) -> str:
    if config.get("chatlog", {}).get("db_path"):
        return config["chatlog"]["db_path"]  # type: ignore[no-any-return]
    base = os.path.join(os.path.dirname(__file__), "..", "..", "chatlog_alpha")
    db_candidates = [
        os.path.join(base, "db_storage", "message"),
        os.path.join(base, "work", "db_storage", "message"),
    ]
    best_db = None
    best_mtime: float = 0
    for chatlog_db in db_candidates:
        if not os.path.exists(chatlog_db):
            continue
        for f in os.listdir(chatlog_db):
            if (
                f.startswith("message_")
                and f.endswith(".db")
                and not f.startswith("message_resource")
            ):
                fp = os.path.join(chatlog_db, f)
                mtime = os.path.getmtime(fp)
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_db = fp
    return best_db or ""


def _build_chatlog_cmd(subcmd: str) -> list:
    global _chatlog_config_cache
    exe = find_chatlog_exe()
    if not exe:
        return []
    cmd = [exe, subcmd]
    config_file = find_chatlog_config()
    if config_file:
        cmd.extend(["--work-dir", os.path.dirname(config_file)])
        if _chatlog_config_cache is None:
            with open(config_file, "r", encoding="utf-8") as f:
                _chatlog_config_cache = json.load(f)
        cfg = _chatlog_config_cache
        if cfg.get("data_dir"):
            cmd.extend(["--data-dir", cfg["data_dir"]])
        if cfg.get("data_key"):
            cmd.extend(["--data-key", cfg["data_key"]])
        if cfg.get("img_key"):
            cmd.extend(["--img-key", cfg["img_key"]])
        if cfg.get("version"):
            cmd.extend(["--version", str(cfg["version"])])
        if cfg.get("platform"):
            cmd.extend(["--platform", cfg["platform"]])
    return cmd


def run_chatlog_decrypt() -> bool:
    cmd = _build_chatlog_cmd("decrypt")
    if not cmd:
        logger.warning("未找到 chatlog.exe，跳过解密")
        return False
    logger.info("执行 chatlog decrypt 更新数据库...")
    try:
        proc = subprocess.run(
            cmd,
            cwd=os.path.dirname(cmd[0]),
            capture_output=True,
            timeout=60,
        )
        if proc.returncode == 0:
            logger.info("chatlog decrypt 完成，数据库已更新")
            return True
        else:
            stderr = proc.stderr.decode("utf-8", errors="replace")[:500]
            logger.warning(
                f"chatlog decrypt 返回非零 (code={proc.returncode}): {stderr}"
            )
            return False
    except subprocess.TimeoutExpired:
        logger.warning("chatlog decrypt 超时（60秒）")
        return False
    except Exception as e:
        logger.error(f"chatlog decrypt 失败: {e}")
        return False


def _forward_output(pipe, level):
    for line in iter(pipe.readline, b""):
        try:
            msg = line.decode("utf-8", errors="replace").rstrip()
            if msg:
                logger.log(level, f"[chatlog] {msg}")
        except Exception:
            logger.debug("转发 chatlog 输出时出错", exc_info=True)
    pipe.close()


def check_chatlog_health() -> None:
    global _chatlog_process
    with _chatlog_lock:
        if _chatlog_process is None:
            return
        if _chatlog_process.poll() is not None:
            logger.warning(
                f"chatlog server 进程已退出 (exit code: {_chatlog_process.returncode})，尝试重启..."
            )
            _chatlog_process = None
    # start_chatlog_server 内部自己加锁，不要在持锁期间调用
    if _chatlog_process is None:
        start_chatlog_server()


def start_chatlog_server() -> Optional[subprocess.Popen]:
    global _chatlog_process
    with _chatlog_lock:
        cmd = _build_chatlog_cmd("server")
        if not cmd:
            logger.warning("未找到 chatlog.exe，跳过自动启动 chatlog server")
            return None
        cmd.append("--auto-decrypt")
        config_file = find_chatlog_config()
        if config_file and _chatlog_config_cache:
            cfg = _chatlog_config_cache
            if cfg.get("http_addr"):
                addr = cfg["http_addr"]
                if not addr.startswith(":"):
                    from chatlens._defaults import DEFAULT_CHATLOG_API_BASE

                    default_port = DEFAULT_CHATLOG_API_BASE.split(":")[-1]
                    addr = (
                        ":" + addr.split(":")[-1] if ":" in addr else f":{default_port}"
                    )
                cmd.extend(["--addr", addr])
        logger.info(f"启动 chatlog server: {' '.join(cmd[:6])}...")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(cmd[0]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32"
                else 0,
            )
            _chatlog_process = proc
            stdout_thread = threading.Thread(
                target=_forward_output, args=(proc.stdout, logging.INFO), daemon=True
            )
            stderr_thread = threading.Thread(
                target=_forward_output, args=(proc.stderr, logging.WARNING), daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()
            time.sleep(2)
            if proc.poll() is not None:
                stderr_out = (
                    proc.stderr.read().decode("utf-8", errors="replace")
                    if proc.stderr
                    else ""
                )
                logger.error(
                    f"chatlog server 启动失败 (exit code {proc.returncode}): {stderr_out[:500]}"
                )
                _chatlog_process = None
                return None
            logger.info(f"chatlog server 已启动 (PID: {proc.pid})")
            return proc
        except Exception as e:
            logger.error(f"启动 chatlog server 失败: {e}")
            _chatlog_process = None
            return None


def stop_chatlog_server() -> None:
    global _chatlog_process
    with _chatlog_lock:
        if _chatlog_process and _chatlog_process.poll() is None:
            logger.info("正在停止 chatlog server...")
            try:
                if sys.platform == "win32":
                    os.kill(_chatlog_process.pid, signal.CTRL_BREAK_EVENT)
                else:
                    _chatlog_process.terminate()
                _chatlog_process.wait(timeout=5)
                logger.info("chatlog server 已停止")
            except Exception:
                logger.debug("停止 chatlog server 时出错，尝试强制终止", exc_info=True)
                try:
                    _chatlog_process.kill()
                    logger.info("chatlog server 已强制停止")
                except Exception:
                    logger.debug("强制终止 chatlog server 也失败", exc_info=True)
            _chatlog_process = None

"""统一日志配置模块"""
import contextvars
import datetime
import json
import logging
import logging.handlers
import os
import uuid
from typing import Optional

# P1 修复 (AC3)：request_id contextvar，所有日志自动附带同一请求的 request_id。
# 用于在并发 web 服务中把一次请求的所有日志（AI/报告/任务）串联起来。
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chatlens_request_id", default="-"
)


def new_request_id() -> str:
    """生成一个新的 request_id（短 8 字符）。"""
    return uuid.uuid4().hex[:8]


def set_request_id(rid: str) -> contextvars.Token:
    """设置当前上下文的 request_id。"""
    return _request_id_var.set(rid)


def reset_request_id(token: contextvars.Token) -> None:
    """还原 request_id（与 set_request_id 配对使用）。"""
    _request_id_var.reset(token)


def current_request_id() -> str:
    """获取当前上下文的 request_id，无则返回 '-'。"""
    return _request_id_var.get()


class RequestIDFilter(logging.Filter):
    """logging.Filter：把 request_id 注入到 record，便于 formatter 引用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


class JSONFormatter(logging.Formatter):
    """P3 修复 (AC4)：结构化 JSON 日志输出。

    每条日志输出 1 行 valid JSON，可被 json.loads(line) 解析。
    必含字段：ts / level / request_id / logger / msg / module / func / line / thread / process
    可选字段：exception（仅 logger.exception 触发时存在，含单行 traceback）

    关键：exc_info 用 self.formatException() 取字符串后，**不**手动 join 多次，
    json.dumps 会把 ``\\n`` 转成字面 ``\\n``，保持单行。
    """

    # 字段顺序：固定 key 排前，运行时字段排后
    DEFAULT_KEYS = (
        "ts",
        "level",
        "request_id",
        "logger",
        "msg",
        "module",
        "func",
        "line",
        "thread",
        "process",
    )

    def format(self, record: logging.LogRecord) -> str:
        # ts: ISO8601 毫秒 + UTC 时区（用本地时区更贴近运维场景；带 offset 即可）
        ts = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc
        ).astimezone().isoformat(timespec="milliseconds")

        payload: dict = {
            "ts": ts,
            "level": record.levelname,
            "request_id": getattr(record, "request_id", "-"),
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "thread": record.threadName,
            "process": record.process,
        }
        if record.exc_info:
            # self.formatException 返回多行 traceback，json.dumps 会转义 \n 为 \\n
            # （注意：这里实际写入的是真 \\n 字符，json.dumps 把它编码为字面 \\n）
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        # ensure_ascii=False 保留中文
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_text_formatter(fmt: Optional[str], datefmt: str) -> logging.Formatter:
    return logging.Formatter(fmt, datefmt)


def _build_formatter(log_format: str) -> logging.Formatter:
    """根据 log_format 选 text / json formatter。"""
    if log_format == "json":
        return JSONFormatter()
    fmt = "%(asctime)s [%(levelname)s] [req=%(request_id)s] %(name)s: %(message)s"
    return _build_text_formatter(fmt, "%Y-%m-%d %H:%M:%S")


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    log_format: Optional[str] = None,
) -> None:
    """配置应用日志

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_file: 日志文件路径，None 则仅输出到控制台
        log_format: 自定义日志格式（向后兼容），"text"/"json" 仍由
                    CHATLENS_LOG_FORMAT 环境变量控制（优先级最高）。
    """
    # 优先级：CHATLENS_LOG_FORMAT 环境变量 > log_format 参数
    fmt_name = os.environ.get("CHATLENS_LOG_FORMAT", "").strip().lower() or (
        log_format or "text"
    )
    if fmt_name not in ("text", "json"):
        fmt_name = "text"

    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = []
    rid_filter = RequestIDFilter()

    # 控制台处理器
    console = logging.StreamHandler()
    if fmt_name == "json":
        console.setFormatter(JSONFormatter())
    else:
        # 文本模式：保留原始 [req=xxx] 格式
        text_fmt = (
            log_format
            if log_format and "%(levelname)s" in log_format
            else "%(asctime)s [%(levelname)s] [req=%(request_id)s] %(name)s: %(message)s"
        )
        console.setFormatter(logging.Formatter(text_fmt, datefmt))
    console.addFilter(rid_filter)
    handlers.append(console)

    # 文件处理器（轮转）
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        if fmt_name == "json":
            file_handler.setFormatter(JSONFormatter())
        else:
            text_fmt = (
                log_format
                if log_format and "%(levelname)s" in log_format
                else "%(asctime)s [%(levelname)s] [req=%(request_id)s] %(name)s: %(message)s"
            )
            file_handler.setFormatter(logging.Formatter(text_fmt, datefmt))
        file_handler.addFilter(rid_filter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )

    # 降低第三方库日志级别
    for noisy in ("urllib3", "httpx", "httpcore", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

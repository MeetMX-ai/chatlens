"""性能/日志反模式静态扫描器 — 用于 CI / 提交前发现常见的运行时性能问题。

支持以下反模式检测：
    R1 紧 while 循环（缺少 sleep / await asyncio.sleep）
    R2 async def 内的同步阻塞 I/O（requests / urllib.request / subprocess / time.sleep）
    R3 logging.basicConfig(level=DEBUG) 未配套降级第三方日志
    R4 for 循环内反复创建 httpx.Client / httpx.AsyncClient
    R5 模块顶层执行 I/O（无 if __name__ == '__main__' 守卫）
    R6 热路径内的 f-string logger.info
    R7 FastAPI() 创建后未挂载 GZipMiddleware 类压缩中间件
    R8 uvicorn.run() 未指定 workers= 参数
    R9 启动脚本（start.bat / *.sh）里执行 `pip install`，每次启动全量安装依赖

使用方式：
    python scripts/perf_anti_patterns.py [--root <path>] [--format text|json] [--fail-on high|medium|low]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# 扫描时需要跳过的目录（虚拟环境、构建产物、依赖等）
EXCLUDED_DIRS: set[str] = {
    ".venv",
    "venv",
    "env",
    "node_modules",
    "build",
    "dist",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "site-packages",
}

# 同步 I/O 调用的检测模式（R2）
SYNC_IO_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brequests\."), "requests.*"),
    (re.compile(r"\burllib\.request\."), "urllib.request.*"),
    (re.compile(r"(?<!await\s)\bsubprocess\.run\("), "subprocess.run("),
    (re.compile(r"(?<!await\s)\bsubprocess\.call\("), "subprocess.call("),
    (re.compile(r"(?<!await\s)\bsubprocess\.check_output\("), "subprocess.check_output("),
    (re.compile(r"(?<!await\s)\bsubprocess\.Popen\("), "subprocess.Popen("),
    # time.sleep 但不是 await time.sleep（asyncio.sleep 在另一条规则中豁免）
    (re.compile(r"(?<!await\s)(?<!\.)\btime\.sleep\("), "time.sleep("),
)

# R1 紧循环允许的"刹车"调用
R1_SLEEP_NAMES: set[str] = {
    "asyncio.sleep",
    "anyio.sleep",
    "time.sleep",
}

# R3 — basicConfig DEBUG 必须配套的第三方日志降级模块名
R3_NOISY_LOGGERS: set[str] = {
    "httpx",
    "urllib3",
    "urllib3.connectionpool",
    "httpcore",
    "multipart",
    "asyncio",
}

# R3 — 视为"已经配套降级" 的标识调用
R3_SETUP_FUNCS: set[str] = {"setup_logging", "dictConfig"}


@dataclass
class Finding:
    """单条反模式命中记录。"""

    rule: str
    severity: str
    file: str
    line: int
    message: str

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "message": self.message,
        }


@dataclass
class FileReport:
    """单个文件的扫描结果。"""

    file: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)


# ────────────────────────── 工具函数 ──────────────────────────


def _is_excluded(path: Path) -> bool:
    """检查路径是否命中 EXCLUDED_DIRS 中的目录。"""
    parts = set(path.parts)
    return bool(parts & EXCLUDED_DIRS)


def _iter_python_files(root: Path) -> Iterable[Path]:
    """遍历 root 下所有 .py 文件（同步生成器）。"""
    for py in root.rglob("*.py"):
        if _is_excluded(py):
            continue
        yield py


def _line_of(node: ast.AST) -> int:
    return getattr(node, "lineno", 0) or 0


def _contains_call_to(tree: ast.AST, names: set[str]) -> bool:
    """tree 中是否包含对 names 中任一名字的（直接）调用。"""

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.found: bool = False

        def visit_Call(self, node: ast.Call) -> None:  # type: ignore[override]
            if isinstance(node.func, ast.Name) and node.func.id in names:
                self.found = True
                return
            if isinstance(node.func, ast.Attribute) and node.func.attr in names:
                # 仅当形如 xxx.setup_logging(...) / logging.dictConfig(...)
                # 我们关心顶层的属性名，所以做宽松匹配
                self.found = True
                return
            self.generic_visit(node)

    v = Visitor()
    v.visit(tree)
    return v.found


def _contains_setLevel_for(tree: ast.AST, targets: set[str]) -> bool:
    """tree 中是否对 targets 中任一 logger 调用了 setLevel。"""

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.found: bool = False

        def visit_Call(self, node: ast.Call) -> None:  # type: ignore[override]
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "setLevel"
            ):
                base = node.func.value
                if isinstance(base, ast.Name) and base.id in targets:
                    self.found = True
                    return
                if (
                    isinstance(base, ast.Call)
                    and isinstance(base.func, ast.Attribute)
                    and base.func.attr == "getLogger"
                ):
                    if (
                        base.args
                        and isinstance(base.args[0], ast.Constant)
                        and isinstance(base.args[0].value, str)
                        and base.args[0].value in targets
                    ):
                        self.found = True
                        return
            self.generic_visit(node)

    v = Visitor()
    v.visit(tree)
    return v.found


# ────────────────────────── 各规则检测器 ──────────────────────────


def _check_r1(source: str, tree: ast.AST) -> list[Finding]:
    """R1: while 紧循环缺少 sleep。

    只在 async 上下文里认为 "while 没 sleep" 才是反模式，
    因为线程里 time.sleep(60) 是合理的（不过我们也允许）。
    这里采用更宽松的策略：只要是 `while True` 或 `while <name>` 且
    循环体里没出现 R1_SLEEP_NAMES 的调用就报。
    """
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.While,)):
            continue
        # 取出循环体的源代码片段以做宽松匹配
        body_src = ast.get_source_segment(source, node) or ""
        if not body_src:
            # 退而求其次用 ast.dump 不实用，直接拼接子节点 lineno
            body_src = "\n".join(
                ast.unparse(child) for child in node.body if hasattr(child, "lineno")
            )
        if not any(name in body_src for name in R1_SLEEP_NAMES):
            findings.append(
                Finding(
                    rule="R1",
                    severity="high",
                    file="",
                    line=_line_of(node),
                    message=(
                        f"紧循环缺少 sleep（{ast.unparse(node.test).strip()}）；"
                        "建议在循环体内加入 `await asyncio.sleep(...)` 或 `time.sleep(...)`"
                    ),
                )
            )
    return findings


def _check_r2(source: str, tree: ast.AST) -> list[Finding]:
    """R2: async def 函数体里出现同步阻塞 I/O。"""
    findings: list[Finding] = []

    def is_inside_async(node: ast.AST) -> bool:
        for parent in getattr(node, "parents", []):  # type: ignore[attr-defined]
            if isinstance(parent, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith)):
                return True
        return False

    class ParentVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[ast.AST] = []

        def visit(self, node: ast.AST) -> None:  # type: ignore[override]
            node.parents = list(self.stack)  # type: ignore[attr-defined]
            self.stack.append(node)
            self.generic_visit(node)
            self.stack.pop()

    ParentVisitor().visit(tree)

    class CallVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # type: ignore[override]
            if is_inside_async(node):
                call_src = ast.unparse(node)
                for pattern, label in SYNC_IO_PATTERNS:
                    if pattern.search(call_src):
                        findings.append(
                            Finding(
                                rule="R2",
                                severity="high",
                                file="",
                                line=_line_of(node),
                                message=(
                                    f"async def 内调用同步阻塞 I/O `{label}`；"
                                    "应使用 httpx/asyncio.create_subprocess_exec 等异步 API，"
                                    "或放入 `await loop.run_in_executor(...)`"
                                ),
                            )
                        )
                        break
            self.generic_visit(node)

    CallVisitor().visit(tree)
    return findings


def _check_r3(source: str, tree: ast.AST, file_text: str) -> list[Finding]:
    """R3: logging.basicConfig(level=DEBUG) 未配套降级第三方日志。

    只看**真实的 AST 调用**（不是源代码里碰巧出现的字符串），
    避免误伤规则本身的源码（脚本里就有 `logging.basicConfig(level=DEBUG)` 的字面量）。
    """
    findings: list[Finding] = []

    def is_debug_basicconfig(call: ast.Call) -> bool:
        # 形如 logging.basicConfig(level=DEBUG) 或 logging.basicConfig(level=logging.DEBUG)
        if not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "basicConfig"
        ):
            return False
        for kw in call.keywords:
            if kw.arg == "level":
                v = kw.value
                if isinstance(v, ast.Name) and v.id == "DEBUG":
                    return True
                if (
                    isinstance(v, ast.Attribute)
                    and v.attr == "DEBUG"
                    and isinstance(v.value, ast.Name)
                    and v.value.id == "logging"
                ):
                    return True
        return False

    def is_at_module_level(call: ast.Call) -> bool:
        # call 必须直接挂在 Module.body 或 If(__name__) 之外的简单语句上
        parents: list[ast.AST] = list(getattr(call, "parents", []))  # type: ignore[attr-defined]
        if not parents:
            return False
        # 最近的"语句容器"不应该是 FunctionDef / ClassDef / AsyncFunctionDef
        for p in reversed(parents):
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return False
        return True

    # 给所有节点挂 parents
    class ParentVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[ast.AST] = []

        def visit(self, node: ast.AST) -> None:  # type: ignore[override]
            node.parents = list(self.stack)  # type: ignore[attr-defined]
            self.stack.append(node)
            self.generic_visit(node)
            self.stack.pop()

    ParentVisitor().visit(tree)

    has_debug_basicconfig = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and is_debug_basicconfig(node) and is_at_module_level(node):
            has_debug_basicconfig = True
            break

    if not has_debug_basicconfig:
        return findings

    # 在整个模块范围内是否调用了 setup_logging / dictConfig
    has_setup = _contains_call_to(tree, R3_SETUP_FUNCS)
    if has_setup:
        return findings

    # 是否对 R3_NOISY_LOGGERS 任意一个调用过 setLevel
    has_downgrade = _contains_setLevel_for(tree, R3_NOISY_LOGGERS)
    if has_downgrade:
        return findings

    findings.append(
        Finding(
            rule="R3",
            severity="high",
            file="",
            line=0,
            message=(
                "检测到 `logging.basicConfig(level=DEBUG)`，但缺少对 "
                "httpx / urllib3 等第三方 logger 的降级，也没有调用 `setup_logging()`。"
                "会把 INFO 级别噪音淹没你的日志。"
            ),
        )
    )
    return findings


def _check_r4(source: str, tree: ast.AST) -> list[Finding]:
    """R4: for 循环里直接 `with httpx.Client(` 或 `httpx.AsyncClient(`。"""
    findings: list[Finding] = []

    def is_httpx_ctor(call: ast.Call) -> bool:
        if not isinstance(call.func, ast.Attribute):
            return False
        return call.func.attr in {"Client", "AsyncClient"} and (
            (isinstance(call.func.value, ast.Name) and call.func.value.id == "httpx")
            or (
                isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "httpx"
            )
        )

    class Visitor(ast.NodeVisitor):
        def visit_For(self, node: ast.For) -> None:  # type: ignore[override]
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and is_httpx_ctor(child):
                    findings.append(
                        Finding(
                            rule="R4",
                            severity="medium",
                            file="",
                            line=_line_of(child),
                            message=(
                                "for 循环内创建 httpx 客户端，连接无法复用。"
                                "建议把 `httpx.Client(...)` 提到循环外（`with` 块）"
                                "或全局复用单个 `AsyncClient`。"
                            ),
                        )
                    )
            self.generic_visit(node)

    Visitor().visit(tree)
    return findings


def _check_r5(source: str, tree: ast.AST) -> list[Finding]:
    """R5: 模块顶层执行 I/O（无 `if __name__ == '__main__'` 守卫）。

    我们把以下"模块级"语句视为可疑 I/O：
        - sqlite3.connect / sqlite3.connect(...)
        - httpx.get/post/...
        - requests.get/post/...
        - urllib.request.urlopen(...)
        - subprocess.run(...)
        - open(..., 'w'/'wb'/'a'/'ab')

    注意：必须**不**下钻到函数/类体里 —— 只看真正与模块同级的语句。
    """
    findings: list[Finding] = []
    guarded = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )

    def is_module_level(node: ast.AST) -> bool:
        return getattr(node, "col_offset", 0) == 0

    IO_PREFIXES = (
        "sqlite3.connect",
        "httpx.",
        "requests.",
        "urllib.request.urlopen",
        "subprocess.run",
    )

    suspicious: list[tuple[int, str]] = []

    def harvest_top_level_calls(stmt: ast.AST) -> None:
        """只从 stmt 的"同级"挑出 call，不下钻到嵌套函数/类。"""
        # 跳过函数/类定义本身 —— 它们在模块级但不是"执行 I/O 的语句"
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        for sub in ast.iter_child_nodes(stmt):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            # 递归到下一层（但仍避开函数/类）
            if isinstance(sub, ast.Call):
                label = ast.unparse(sub.func)
                if any(label.startswith(prefix) for prefix in IO_PREFIXES):
                    suspicious.append((_line_of(sub), label))
                if (
                    isinstance(sub.func, ast.Name)
                    and sub.func.id == "open"
                    and len(sub.args) >= 2
                    and isinstance(sub.args[1], ast.Constant)
                ):
                    mode = str(sub.args[1].value)
                    if any(ch in mode for ch in ("w", "a")):
                        suspicious.append((_line_of(sub), f"open(..., {mode!r})"))
            else:
                # 继续向下但避开函数/类
                for grand in ast.walk(sub):
                    if isinstance(grand, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                        continue
                    if isinstance(grand, ast.Call):
                        label = ast.unparse(grand.func)
                        if any(label.startswith(prefix) for prefix in IO_PREFIXES):
                            suspicious.append((_line_of(grand), label))
                        if (
                            isinstance(grand.func, ast.Name)
                            and grand.func.id == "open"
                            and len(grand.args) >= 2
                            and isinstance(grand.args[1], ast.Constant)
                        ):
                            mode = str(grand.args[1].value)
                            if any(ch in mode for ch in ("w", "a")):
                                suspicious.append((_line_of(grand), f"open(..., {mode!r})"))

    for stmt in tree.body:
        if not is_module_level(stmt):
            continue
        harvest_top_level_calls(stmt)

    if suspicious and not guarded:
        for line, label in suspicious:
            findings.append(
                Finding(
                    rule="R5",
                    severity="medium",
                    file="",
                    line=line,
                    message=(
                        f"模块顶层执行 `{label}`（无 `if __name__ == '__main__':` 守卫），"
                        "导入即触发 I/O。建议把初始化代码放进函数 / `__main__` 块。"
                    ),
                )
            )
    return findings


def _check_r6(source: str, tree: ast.AST) -> list[Finding]:
    """R6: 热路径（while/for 循环体）内的 f-string `logger.info(...)`。"""
    findings: list[Finding] = []

    class Visitor(ast.NodeVisitor):
        def visit_While(self, node: ast.While) -> None:  # type: ignore[override]
            self._scan_loop_body(node, node.body)
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> None:  # type: ignore[override]
            self._scan_loop_body(node, node.body)
            self.generic_visit(node)

        def _scan_loop_body(
            self, loop_node: ast.AST, body: Sequence[ast.stmt]
        ) -> None:
            for sub in body:
                for call in ast.walk(sub):
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr in {"info", "debug", "warning"}
                    ):
                        # logger.info / logger.debug / logger.warning
                        if isinstance(call.func.value, ast.Name) and call.func.value.id in {
                            "logger",
                            "logging",
                            "_logger",
                        }:
                            args = call.args
                            if args and isinstance(args[0], ast.JoinedStr):
                                findings.append(
                                    Finding(
                                        rule="R6",
                                        severity="medium",
                                        file="",
                                        line=_line_of(call),
                                        message=(
                                            "热路径循环内的 f-string 日志会产生大量格式化开销。"
                                            "建议用 `logger.info(\"...\", arg)` 延迟格式化，"
                                            "并加 throttle（例如每 N 次迭代记一次）。"
                                        ),
                                    )
                                )

    Visitor().visit(tree)
    return findings


def _check_r7(source: str, tree: ast.AST) -> list[Finding]:
    """R7: FastAPI() 创建后未挂载 GZipMiddleware（或等价压缩中间件）。

    我们用一种宽口径的判定：模块里出现 FastAPI( 调用，且整个模块里
    找不到 `add_middleware(GZipMiddleware` 或 `add_middleware(BrotliMiddleware` 的痕迹。
    """
    findings: list[Finding] = []
    has_fastapi_ctor = False
    has_compression_mw = False

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "FastAPI"
        ):
            has_fastapi_ctor = True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_middleware"
        ):
            arg_src = ast.unparse(node)
            if "GZipMiddleware" in arg_src or "BrotliMiddleware" in arg_src:
                has_compression_mw = True

    if has_fastapi_ctor and not has_compression_mw:
        findings.append(
            Finding(
                rule="R7",
                severity="low",
                file="",
                line=0,
                message=(
                    "检测到 `FastAPI(` 但未挂载 GZipMiddleware（或 BrotliMiddleware）。"
                    "响应体未压缩会增加网络传输量。"
                ),
            )
        )
    return findings


def _check_r8(source: str, tree: ast.AST) -> list[Finding]:
    """R8: uvicorn.run() 未指定 workers=（开发模式 OK，CI 给提示）。"""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "uvicorn"
        ):
            has_workers = any(
                (kw.arg == "workers" or kw.arg == "worker_class")
                for kw in node.keywords
            )
            if not has_workers:
                findings.append(
                    Finding(
                        rule="R8",
                        severity="low",
                        file="",
                        line=_line_of(node),
                        message=(
                            "`uvicorn.run()` 未指定 `workers=`；生产环境建议用 "
                            "`uvicorn.run(..., workers=N)` 或外层 gunicorn 启动多 worker。"
                        ),
                    )
                )
    return findings


# R9: 启动脚本（start.bat / *.sh）里执行 `pip install`
# 仅作为 low 严重度提示，不在 CI 中失败（默认 fail-on=none 即可）。
R9_STARTUP_SCRIPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("start.bat", re.compile(r"(?i)(^|\s|&)\s*pip\s+install\b")),
    ("*.sh", re.compile(r"(^|\s|;)\s*pip\s+install\b")),
    ("*.sh", re.compile(r"(^|\s|;)\s*pip3\s+install\b")),
)


def _iter_startup_scripts(root: Path) -> Iterable[tuple[Path, str]]:
    """遍历项目根下 start.bat / *.sh 启动脚本（不递归子目录）。"""
    for entry in sorted(root.iterdir()):
        if _is_excluded(entry):
            continue
        if not entry.is_file():
            continue
        if entry.name == "start.bat" or entry.suffix == ".sh":
            yield entry, entry.suffix or ".bat"


def _check_r9(root: Path) -> list[Finding]:
    """R9: 启动脚本里出现 `pip install`，每次启动都会全量重装依赖。"""
    findings: list[Finding] = []
    for path, _label in _iter_startup_scripts(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for _label, pattern in R9_STARTUP_SCRIPT_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        Finding(
                            rule="R9",
                            severity="low",
                            file=str(path),
                            line=line_no,
                            message=(
                                f"启动脚本 `{path.name}` 第 {line_no} 行执行了 `pip install`。"
                                "每次启动都会全量重装依赖，拖慢冷启动。"
                                "建议只在新依赖变化时手动执行，或使用 `pip install --no-deps` + 缓存。"
                            ),
                        )
                    )
                    break
    return findings


# ────────────────────────── 顶层调度 ──────────────────────────


SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def scan_file(path: Path) -> FileReport:
    """对单个 .py 文件跑全部规则。"""
    report = FileReport(file=str(path))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return report
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        # 语法错误的文件跳过（CI 里其他工具会处理）
        return report

    raw_findings: list[Finding] = []
    raw_findings.extend(_check_r1(text, tree))
    raw_findings.extend(_check_r2(text, tree))
    raw_findings.extend(_check_r3(text, tree, text))
    raw_findings.extend(_check_r4(text, tree))
    raw_findings.extend(_check_r5(text, tree))
    raw_findings.extend(_check_r6(text, tree))
    raw_findings.extend(_check_r7(text, tree))
    raw_findings.extend(_check_r8(text, tree))

    for f in raw_findings:
        f.file = str(path)
        report.add(f)
    return report


def scan_root(root: Path) -> list[FileReport]:
    """扫描整个 root 目录。"""
    reports: list[FileReport] = []
    for py in _iter_python_files(root):
        r = scan_file(py)
        if r.findings:
            reports.append(r)
    # R9: 启动脚本是 .bat/.sh，不是 .py，单独收集成伪 FileReport
    r9_report = FileReport(file="<startup-scripts>")
    for f in _check_r9(root):
        r9_report.add(f)
    if r9_report.findings:
        reports.append(r9_report)
    reports.sort(key=lambda r: r.file)
    return reports


# ────────────────────────── 报告输出 ──────────────────────────


def render_text(reports: Sequence[FileReport]) -> str:
    if not reports:
        return "✅ 未发现性能/日志反模式。\n"
    lines: list[str] = []
    lines.append(f"⚠️  共发现 {sum(len(r.findings) for r in reports)} 条命中，"
                 f"涉及 {len(reports)} 个文件：\n")
    for r in reports:
        lines.append(f"── {r.file}")
        for f in r.findings:
            loc = f"line {f.line}" if f.line else "module"
            lines.append(f"   [{f.severity:6s}] {f.rule} @ {loc}: {f.message}")
        lines.append("")
    return "\n".join(lines)


def render_json(reports: Sequence[FileReport]) -> str:
    payload = {
        "summary": {
            "total": sum(len(r.findings) for r in reports),
            "files": len(reports),
            "by_severity": {
                sev: sum(
                    1
                    for r in reports
                    for f in r.findings
                    if f.severity == sev
                )
                for sev in ("high", "medium", "low")
            },
        },
        "files": [
            {
                "file": r.file,
                "findings": [f.to_dict() for f in r.findings],
            }
            for r in reports
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ────────────────────────── CLI ──────────────────────────


def _default_root() -> Path:
    """默认扫描根目录：脚本的父目录的父目录（即项目根）。"""
    return Path(__file__).resolve().parent.parent


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="性能/日志反模式静态扫描器（CI 友好）",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_default_root(),
        help="扫描根目录（默认：项目根）",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="输出格式（默认 text）",
    )
    parser.add_argument(
        "--fail-on",
        choices=("high", "medium", "low", "none"),
        default="none",
        help="达到该严重度时 exit 1（默认不基于严重度失败）",
    )
    args = parser.parse_args(argv)

    root: Path = args.root
    if not root.exists():
        print(f"❌ 扫描根目录不存在: {root}", file=sys.stderr)
        return 2

    reports = scan_root(root)
    output = render_text(reports) if args.format == "text" else render_json(reports)
    print(output)

    if args.fail_on != "none":
        threshold = SEVERITY_RANK[args.fail_on]
        max_seen = max(
            (SEVERITY_RANK[f.severity] for r in reports for f in r.findings),
            default=-1,
        )
        if max_seen >= threshold:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

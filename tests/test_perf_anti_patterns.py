"""perf_anti_patterns 扫描器的单元测试。

我们尽量不依赖项目其它代码，直接用临时文件喂给扫描器。
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

# 把 scripts/ 加到 sys.path 以便 import
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ast  # noqa: E402

from perf_anti_patterns import (  # noqa: E402  (sys.path mutation above)
    _check_r1,
    _check_r2,
    _check_r3,
    _check_r4,
    _check_r5,
    _check_r6,
    _check_r7,
    _check_r8,
    main,
    render_json,
    render_text,
    scan_file,
    scan_root,
)


def _parse(src: str) -> ast.AST:
    return ast.parse(textwrap.dedent(src))


def _names(findings):
    return {f.rule for f in findings}


class TestR1TightLoop(unittest.TestCase):
    def test_while_true_pass_is_flagged(self):
        src = "while True:\n    pass\n"
        tree = _parse(src)
        findings = _check_r1(src, tree)
        rules = _names(findings)
        self.assertIn("R1", rules)

    def test_while_with_break_but_no_sleep_is_flagged(self):
        src = "while running:\n    do_thing()\n    if done:\n        break\n"
        tree = _parse(src)
        findings = _check_r1(src, tree)
        self.assertIn("R1", _names(findings))

    def test_while_with_sleep_is_not_flagged(self):
        src = (
            "while running:\n"
            "    do_thing()\n"
            "    time.sleep(1)\n"
        )
        tree = _parse(src)
        findings = _check_r1(src, tree)
        self.assertNotIn("R1", _names(findings))

    def test_while_with_await_asyncio_sleep_is_not_flagged(self):
        src = (
            "async def loop():\n"
            "    while running:\n"
            "        await asyncio.sleep(1)\n"
        )
        tree = _parse(src)
        findings = _check_r1(src, tree)
        self.assertNotIn("R1", _names(findings))


class TestR2SyncIOInAsync(unittest.TestCase):
    def test_requests_get_in_async_def_is_flagged(self):
        src = (
            "import requests\n"
            "async def f():\n"
            "    requests.get('http://example.com')\n"
        )
        tree = _parse(src)
        findings = _check_r2(src, tree)
        self.assertIn("R2", _names(findings))

    def test_subprocess_run_in_async_def_is_flagged(self):
        src = (
            "import subprocess\n"
            "async def f():\n"
            "    subprocess.run(['echo', 'hi'])\n"
        )
        tree = _parse(src)
        findings = _check_r2(src, tree)
        self.assertIn("R2", _names(findings))

    def test_time_sleep_in_async_def_is_flagged(self):
        src = (
            "import time\n"
            "async def f():\n"
            "    time.sleep(1)\n"
        )
        tree = _parse(src)
        findings = _check_r2(src, tree)
        self.assertIn("R2", _names(findings))

    def test_urllib_request_in_async_def_is_flagged(self):
        src = (
            "import urllib.request\n"
            "async def f():\n"
            "    urllib.request.urlopen('http://example.com')\n"
        )
        tree = _parse(src)
        findings = _check_r2(src, tree)
        self.assertIn("R2", _names(findings))

    def test_await_time_sleep_in_async_def_is_not_flagged(self):
        # await time.sleep 在语义上不存在，但 `await asyncio.sleep` 不应触发
        src = (
            "import asyncio\n"
            "async def f():\n"
            "    await asyncio.sleep(1)\n"
        )
        tree = _parse(src)
        findings = _check_r2(src, tree)
        self.assertNotIn("R2", _names(findings))

    def test_requests_in_sync_function_is_not_flagged(self):
        src = (
            "import requests\n"
            "def f():\n"
            "    requests.get('http://example.com')\n"
        )
        tree = _parse(src)
        findings = _check_r2(src, tree)
        self.assertNotIn("R2", _names(findings))


class TestR3BasicConfigDebug(unittest.TestCase):
    def test_basic_config_debug_alone_is_flagged(self):
        src = (
            "import logging\n"
            "logging.basicConfig(level=DEBUG)\n"
        )
        tree = _parse(src)
        findings = _check_r3(src, tree, textwrap.dedent(src))
        self.assertIn("R3", _names(findings))

    def test_basic_config_debug_with_setup_logging_is_not_flagged(self):
        src = (
            "import logging\n"
            "from chatlens.logging_config import setup_logging\n"
            "setup_logging()\n"
            "logging.basicConfig(level=logging.DEBUG)\n"
        )
        tree = _parse(src)
        findings = _check_r3(src, tree, textwrap.dedent(src))
        self.assertNotIn("R3", _names(findings))

    def test_basic_config_debug_with_httpx_setlevel_is_not_flagged(self):
        src = (
            "import logging\n"
            "logging.basicConfig(level=DEBUG)\n"
            "logging.getLogger('httpx').setLevel(logging.WARNING)\n"
        )
        tree = _parse(src)
        findings = _check_r3(src, tree, textwrap.dedent(src))
        self.assertNotIn("R3", _names(findings))


class TestR4HttpxInLoop(unittest.TestCase):
    def test_httpx_client_inside_for_is_flagged(self):
        src = (
            "import httpx\n"
            "for url in urls:\n"
            "    with httpx.Client() as c:\n"
            "        c.get(url)\n"
        )
        tree = _parse(src)
        findings = _check_r4(src, tree)
        self.assertIn("R4", _names(findings))

    def test_httpx_client_outside_for_is_not_flagged(self):
        src = (
            "import httpx\n"
            "client = httpx.Client()\n"
            "for url in urls:\n"
            "    client.get(url)\n"
        )
        tree = _parse(src)
        findings = _check_r4(src, tree)
        self.assertNotIn("R4", _names(findings))


class TestR5ModuleLevelIO(unittest.TestCase):
    def test_top_level_sqlite_connect_is_flagged(self):
        src = (
            "import sqlite3\n"
            "conn = sqlite3.connect('db.sqlite')\n"
        )
        tree = _parse(src)
        findings = _check_r5(src, tree)
        self.assertIn("R5", _names(findings))

    def test_top_level_open_write_is_flagged(self):
        src = (
            "f = open('out.txt', 'w')\n"
            "f.write('x')\n"
        )
        tree = _parse(src)
        findings = _check_r5(src, tree)
        self.assertIn("R5", _names(findings))

    def test_top_level_io_with_main_guard_is_not_flagged(self):
        src = (
            "import sqlite3\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    conn = sqlite3.connect('db.sqlite')\n"
        )
        tree = _parse(src)
        findings = _check_r5(src, tree)
        self.assertNotIn("R5", _names(findings))


class TestR6HotPathLog(unittest.TestCase):
    def test_fstring_logger_info_in_for_loop_is_flagged(self):
        src = (
            "import logging\n"
            "logger = logging.getLogger('x')\n"
            "for i in range(10):\n"
            "    logger.info(f'i = {i}')\n"
        )
        tree = _parse(src)
        findings = _check_r6(src, tree)
        self.assertIn("R6", _names(findings))

    def test_plain_string_logger_info_in_for_loop_is_not_flagged(self):
        src = (
            "import logging\n"
            "logger = logging.getLogger('x')\n"
            "for i in range(10):\n"
            "    logger.info('static message')\n"
        )
        tree = _parse(src)
        findings = _check_r6(src, tree)
        self.assertNotIn("R6", _names(findings))


class TestR7GzipMiddleware(unittest.TestCase):
    def test_fastapi_without_gzip_is_flagged(self):
        src = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
        )
        tree = _parse(src)
        findings = _check_r7(src, tree)
        self.assertIn("R7", _names(findings))

    def test_fastapi_with_gzip_is_not_flagged(self):
        src = (
            "from fastapi import FastAPI\n"
            "from fastapi.middleware.gzip import GZipMiddleware\n"
            "app = FastAPI()\n"
            "app.add_middleware(GZipMiddleware, minimum_size=500)\n"
        )
        tree = _parse(src)
        findings = _check_r7(src, tree)
        self.assertNotIn("R7", _names(findings))


class TestR8UvicornWorkers(unittest.TestCase):
    def test_uvicorn_run_without_workers_is_flagged(self):
        src = (
            "import uvicorn\n"
            "uvicorn.run('app:app', host='0.0.0.0', port=8000)\n"
        )
        tree = _parse(src)
        findings = _check_r8(src, tree)
        self.assertIn("R8", _names(findings))

    def test_uvicorn_run_with_workers_is_not_flagged(self):
        src = (
            "import uvicorn\n"
            "uvicorn.run('app:app', host='0.0.0.0', port=8000, workers=4)\n"
        )
        tree = _parse(src)
        findings = _check_r8(src, tree)
        self.assertNotIn("R8", _names(findings))


class TestCleanFile(unittest.TestCase):
    """完全干净的文件，所有规则都不应命中。"""

    CLEAN = textwrap.dedent(
        """
        import asyncio
        import logging

        logger = logging.getLogger(__name__)


        async def polite_loop() -> None:
            running = True
            while running:
                await asyncio.sleep(1)
                logger.info("tick")
        """
    )

    def test_clean_file_no_findings(self):
        tree = _parse(self.CLEAN)
        findings = []
        findings.extend(_check_r1(self.CLEAN, tree))
        findings.extend(_check_r2(self.CLEAN, tree))
        findings.extend(_check_r3(self.CLEAN, tree, self.CLEAN))
        findings.extend(_check_r4(self.CLEAN, tree))
        findings.extend(_check_r5(self.CLEAN, tree))
        findings.extend(_check_r6(self.CLEAN, tree))
        findings.extend(_check_r7(self.CLEAN, tree))
        findings.extend(_check_r8(self.CLEAN, tree))
        self.assertEqual(findings, [], f"clean file should not hit anything: {findings}")


class TestEndToEndScanFile(unittest.TestCase):
    """把代码写到临时 .py 文件里，跑 scan_file 验证 R1/R2/R3。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, content: str) -> Path:
        p = self.tmpdir / name
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_end_to_end_r1(self):
        p = self._write("tight.py", "while True:\n    pass\n")
        report = scan_file(p)
        self.assertTrue(any(f.rule == "R1" for f in report.findings))

    def test_end_to_end_r2(self):
        p = self._write(
            "async_block.py",
            "import requests\n"
            "async def f():\n"
            "    requests.get('http://example.com')\n",
        )
        report = scan_file(p)
        self.assertTrue(any(f.rule == "R2" for f in report.findings))

    def test_end_to_end_r3(self):
        p = self._write(
            "loud.py",
            "import logging\nlogging.basicConfig(level=DEBUG)\n",
        )
        report = scan_file(p)
        self.assertTrue(any(f.rule == "R3" for f in report.findings))

    def test_end_to_end_clean(self):
        p = self._write(
            "clean.py",
            "import asyncio\n"
            "async def loop():\n"
            "    while True:\n"
            "        await asyncio.sleep(1)\n",
        )
        report = scan_file(p)
        self.assertEqual(report.findings, [])


class TestScanRootAndRenderers(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        (self.tmpdir / "a.py").write_text(
            "while True:\n    pass\n", encoding="utf-8"
        )
        # 不应被扫描
        venv = self.tmpdir / ".venv"
        venv.mkdir()
        (venv / "ignored.py").write_text(
            "while True:\n    pass\n", encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_scan_root_skips_venv(self):
        reports = scan_root(self.tmpdir)
        files = {r.file for r in reports}
        self.assertEqual(len(reports), 1)
        for f in files:
            self.assertNotIn(".venv", f)

    def test_render_text(self):
        reports = scan_root(self.tmpdir)
        text = render_text(reports)
        self.assertIn("R1", text)

    def test_render_json(self):
        reports = scan_root(self.tmpdir)
        import json as _json

        payload = _json.loads(render_json(reports))
        self.assertIn("summary", payload)
        self.assertGreaterEqual(payload["summary"]["total"], 1)


class TestCLI(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        (self.tmpdir / "tight.py").write_text(
            "while True:\n    pass\n", encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_cli_fail_on_high(self):
        rc = main(["--root", str(self.tmpdir), "--format", "json", "--fail-on", "high"])
        self.assertEqual(rc, 1)

    def test_cli_no_fail_when_disabled(self):
        rc = main(["--root", str(self.tmpdir), "--format", "text", "--fail-on", "none"])
        self.assertEqual(rc, 0)

    def test_cli_clean_dir_exits_zero(self):
        import tempfile

        with tempfile.TemporaryDirectory() as clean:
            (Path(clean) / "ok.py").write_text("x = 1\n", encoding="utf-8")
            rc = main(["--root", str(Path(clean)), "--format", "text", "--fail-on", "high"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

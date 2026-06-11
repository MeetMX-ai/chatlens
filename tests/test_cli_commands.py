"""CLIService / CLI commands 单元测试 — mock 所有外部依赖"""

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from chatlens.plugins.cli.commands import CLIService, _fmt_table, main


class TestFmtTable(unittest.TestCase):
    """测试 _fmt_table 辅助函数"""

    def test_basic_table(self):
        result = _fmt_table(["Name", "Age"], [["Alice", 30], ["Bob", 25]])
        lines = result.split("\n")
        self.assertEqual(len(lines), 4)  # header + sep + 2 rows
        self.assertIn("Alice", lines[2])
        self.assertIn("Bob", lines[3])

    def test_custom_widths(self):
        result = _fmt_table(["A", "B"], [["x", "y"]], widths=[10, 5])
        lines = result.split("\n")
        self.assertEqual(len(lines[0]), 10 + 2 + 5)  # 10 + "  " + 5

    def test_empty_rows(self):
        result = _fmt_table(["Col"], [])
        lines = result.split("\n")
        self.assertEqual(len(lines), 2)  # header + sep only


def _make_ga(**overrides):
    """创建 mock GroupAnalysis 对象"""
    ga = MagicMock()
    ga.config = {}
    ga.web = MagicMock()
    ga.report = MagicMock()
    for k, v in overrides.items():
        setattr(ga, k, v)
    return ga


def _make_args(**kwargs):
    """创建 argparse.Namespace"""
    return argparse.Namespace(**kwargs)


class TestCLIServiceInit(unittest.TestCase):
    """测试 CLIService 初始化"""

    def test_init_stores_ga(self):
        ga = MagicMock()
        svc = CLIService(ga)
        self.assertIs(svc.ga, ga)


class TestCmdAnalyze(unittest.TestCase):
    """测试 cmd_analyze() — 分析命令"""

    def test_analyze_success(self):
        ga = _make_ga()
        ga.web.auto_analyze.return_value = {
            "success": True,
            "method": "rule",
            "report": {"html_url": "http://x.com/r.html", "image_url": "http://x.com/r.jpg"},
        }
        svc = CLIService(ga)
        args = _make_args(group="test_group", theme="scrapbook", format="jpg",
                          start_date="", end_date="")
        with patch("builtins.print") as mock_print:
            svc.cmd_analyze(args)
        ga.web.auto_analyze.assert_called_once_with(
            "test_group", theme="scrapbook", fmt="jpg", start_date="", end_date=""
        )
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("分析完成", output)

    def test_analyze_failure(self):
        ga = _make_ga()
        ga.web.auto_analyze.return_value = {"success": False, "error": "no data"}
        svc = CLIService(ga)
        args = _make_args(group="g", theme="scrapbook", format="jpg",
                          start_date="", end_date="")
        with patch("builtins.print") as mock_print:
            svc.cmd_analyze(args)
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("分析失败", output)

    def test_analyze_no_web(self):
        ga = _make_ga()
        ga.web = None
        svc = CLIService(ga)
        args = _make_args(group="g", theme="scrapbook", format="jpg",
                          start_date="", end_date="")
        with patch("builtins.print") as mock_print:
            svc.cmd_analyze(args)
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Web 插件未启用", output)

    def test_analyze_with_date_range(self):
        ga = _make_ga()
        ga.web.auto_analyze.return_value = {"success": True, "method": "rule", "report": {}}
        svc = CLIService(ga)
        args = _make_args(group="g", theme="classic", format="html",
                          start_date="2026-01-01", end_date="2026-06-01")
        with patch("builtins.print"):
            svc.cmd_analyze(args)
        ga.web.auto_analyze.assert_called_once_with(
            "g", theme="classic", fmt="html",
            start_date="2026-01-01", end_date="2026-06-01"
        )


class TestCmdReports(unittest.TestCase):
    """测试 cmd_reports() — 报告列表"""

    def test_reports_with_data(self):
        ga = _make_ga()
        ga.report.list_reports.return_value = {
            "reports": [
                {"filename": "r1.html", "size_kb": 100, "created_at": "2026-06-01T10:00:00"},
                {"filename": "r2.jpg", "size_kb": 50, "created_at": "2026-06-02T11:00:00"},
            ]
        }
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_reports(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("r1.html", output)
        self.assertIn("r2.jpg", output)

    def test_reports_empty(self):
        ga = _make_ga()
        ga.report.list_reports.return_value = {"reports": []}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_reports(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("暂无报告", output)

    def test_reports_no_plugin(self):
        ga = _make_ga()
        ga.report = None
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_reports(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Report 插件未启用", output)


class TestCmdReportDelete(unittest.TestCase):
    """测试 cmd_report_delete()"""

    def test_delete_success(self):
        ga = _make_ga()
        ga.report.delete_report.return_value = {"success": True}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_report_delete(_make_args(filename="r1.html"))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("已删除", output)

    def test_delete_failure(self):
        ga = _make_ga()
        ga.report.delete_report.return_value = {"success": False, "error": "not found"}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_report_delete(_make_args(filename="missing.html"))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("删除失败", output)


class TestCmdSchedule(unittest.TestCase):
    """测试定时任务命令"""

    def test_schedule_create_success(self):
        ga = _make_ga()
        ga.web.create_scheduled_task.return_value = {
            "success": True,
            "task_id": "t1",
            "task": {"group_name": "test_group", "hour": 8, "minute": 30},
        }
        svc = CLIService(ga)
        args = _make_args(group="test_group", hour=8, minute=30, theme="scrapbook", format="jpg")
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_create(args)
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("定时任务已创建", output)
        self.assertIn("t1", output)

    def test_schedule_create_failure(self):
        ga = _make_ga()
        ga.web.create_scheduled_task.return_value = {"success": False, "error": "dup"}
        svc = CLIService(ga)
        args = _make_args(group="g", hour=8, minute=0, theme="scrapbook", format="jpg")
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_create(args)
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("创建失败", output)

    def test_schedule_list_with_tasks(self):
        ga = _make_ga()
        ga.web.list_scheduled_tasks.return_value = {
            "tasks": [
                {"task_id": "t1", "group_name": "g1", "hour": 8, "minute": 0,
                 "enabled": True, "status": "idle", "last_run": "2026-06-01T08:00:00"},
            ]
        }
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_list(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("t1", output)
        self.assertIn("g1", output)

    def test_schedule_list_empty(self):
        ga = _make_ga()
        ga.web.list_scheduled_tasks.return_value = {"tasks": []}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_list(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("暂无定时任务", output)

    def test_schedule_trigger(self):
        ga = _make_ga()
        ga.web.trigger_scheduled_task.return_value = {"success": True}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_trigger(_make_args(task_id="t1"))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("已触发", output)

    def test_schedule_delete(self):
        ga = _make_ga()
        ga.web.delete_scheduled_task.return_value = {"success": True}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_delete(_make_args(task_id="t1"))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("已删除", output)

    def test_schedule_toggle_enable(self):
        ga = _make_ga()
        ga.web.toggle_scheduled_task.return_value = {"success": True}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_toggle(_make_args(task_id="t1", enabled=True))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("启用", output)

    def test_schedule_toggle_disable(self):
        ga = _make_ga()
        ga.web.toggle_scheduled_task.return_value = {"success": True}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_toggle(_make_args(task_id="t1", enabled=False))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("禁用", output)

    def test_schedule_no_web(self):
        ga = _make_ga()
        ga.web = None
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_schedule_create(_make_args(group="g", hour=8, minute=0, theme="s", format="j"))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Web 插件未启用", output)


class TestCmdStatus(unittest.TestCase):
    """测试 cmd_status() — 状态查看"""

    def test_status_all_connected(self):
        ga = _make_ga()
        ga.web.get_status.return_value = {
            "chatlog_available": True,
            "chatlog_talkers_count": 5,
            "ai_configured": True,
            "ai_provider": "openai",
            "report_count": 3,
            "report_total_size_kb": 1024,
        }
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_status(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("已连接", output)
        self.assertIn("5", output)
        self.assertIn("openai", output)

    def test_status_disconnected(self):
        ga = _make_ga()
        ga.web.get_status.return_value = {
            "chatlog_available": False,
            "chatlog_talkers_count": 0,
            "ai_configured": False,
            "ai_provider": "",
            "report_count": 0,
            "report_total_size_kb": 0,
        }
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_status(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("未连接", output)
        self.assertIn("未配置", output)

    def test_status_no_web(self):
        ga = _make_ga()
        ga.web = None
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_status(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Web 插件未启用", output)


class TestCmdHealth(unittest.TestCase):
    """测试 cmd_health()"""

    def test_health_ok(self):
        ga = _make_ga()
        ga.web.get_health.return_value = {
            "status": "ok",
            "uptime": "2h",
            "memory_mb": 128.5,
            "chatlog_available": True,
            "scheduled_tasks": 2,
            "recent_errors": [],
        }
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_health(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("ok", output)
        self.assertIn("2h", output)

    def test_health_with_errors(self):
        ga = _make_ga()
        ga.web.get_health.return_value = {
            "status": "degraded",
            "uptime": "1h",
            "memory_mb": 50.0,
            "chatlog_available": False,
            "scheduled_tasks": 0,
            "recent_errors": ["error1", "error2"],
        }
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_health(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("error1", output)
        self.assertIn("error2", output)


class TestCmdGroups(unittest.TestCase):
    """测试 cmd_groups()"""

    def test_groups_with_data(self):
        ga = _make_ga()
        ga.web.get_groups.return_value = {
            "groups": True,
            "group_info": [
                {"label": "群1", "value": "g1@chatroom"},
                {"label": "群2", "value": "g2@chatroom"},
            ],
        }
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_groups(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("群1", output)
        self.assertIn("g2@chatroom", output)

    def test_groups_empty(self):
        ga = _make_ga()
        ga.web.get_groups.return_value = {"groups": []}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_groups(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("暂无群聊数据", output)

    def test_groups_no_web(self):
        ga = _make_ga()
        ga.web = None
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_groups(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("暂无群聊数据", output)


class TestCmdChatlog(unittest.TestCase):
    """测试 chatlog 子命令"""

    def test_chatlog_talkers(self):
        ga = _make_ga()
        ga.web.get_chatlog_talkers.return_value = {
            "talkers": [
                {"talker": "g1@chatroom", "display_name": "群1", "message_count": 100},
                {"talker": "user1", "display_name": "用户1", "message_count": 50},
            ]
        }
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_chatlog_talkers(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("1 个群聊", output)
        self.assertIn("g1@chatroom", output)

    def test_chatlog_talkers_empty(self):
        ga = _make_ga()
        ga.web.get_chatlog_talkers.return_value = {"talkers": []}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_chatlog_talkers(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("chatlog 无可用数据", output)

    def test_chatlog_load_success(self):
        ga = _make_ga()
        ga.web.load_from_chatlog.return_value = {"success": True, "message_count": 500}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_chatlog_load(_make_args(talker="g1@chatroom", limit=0))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("已加载", output)
        self.assertIn("500", output)

    def test_chatlog_load_failure(self):
        ga = _make_ga()
        ga.web.load_from_chatlog.return_value = {"success": False, "error": "not found"}
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_chatlog_load(_make_args(talker="missing@chatroom", limit=0))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("加载失败", output)

    def test_chatlog_load_no_web(self):
        ga = _make_ga()
        ga.web = None
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_chatlog_load(_make_args(talker="g1@chatroom", limit=0))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Web 插件未启用", output)

    @patch("chatlens.plugins.cli.commands.run_chatlog_decrypt", return_value=True)
    def test_chatlog_decrypt_success(self, mock_decrypt):
        ga = _make_ga()
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_chatlog_decrypt(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("解密成功", output)

    @patch("chatlens.plugins.cli.commands.run_chatlog_decrypt", return_value=False)
    def test_chatlog_decrypt_failure(self, mock_decrypt):
        ga = _make_ga()
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_chatlog_decrypt(_make_args())
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("解密失败", output)


class TestCmdServe(unittest.TestCase):
    """测试 cmd_serve()"""

    @patch("chatlens.plugins.web.handler.run_server")
    def test_serve_default(self, mock_run_server):
        ga = _make_ga()
        ga.config = {}
        svc = CLIService(ga)
        args = _make_args(host=None, port=None, no_chatlog=False, no_decrypt=False, debug=False)
        svc.cmd_serve(args)
        mock_run_server.assert_called_once()

    @patch("chatlens.plugins.web.handler.run_server")
    def test_serve_custom_host_port(self, mock_run_server):
        ga = _make_ga()
        ga.config = {}
        svc = CLIService(ga)
        args = _make_args(host="0.0.0.0", port=9090, no_chatlog=False, no_decrypt=False, debug=False)
        svc.cmd_serve(args)
        mock_run_server.assert_called_once_with(ga, host="0.0.0.0", port=9090)

    def test_serve_no_web(self):
        ga = _make_ga()
        ga.web = None
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_serve(_make_args(host=None, port=None))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Web 插件未启用", output)


class TestCmdMcp(unittest.TestCase):
    """测试 cmd_mcp()"""

    def test_mcp_no_plugin(self):
        ga = _make_ga()
        ga.mcp = None
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.cmd_mcp(_make_args(transport="stdio"))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("MCP 插件未启用", output)


class TestBuildParser(unittest.TestCase):
    """测试参数解析器构建"""

    def test_parser_prog_name(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        self.assertEqual(parser.prog, "wxcli")

    def test_parse_analyze_args(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["analyze", "mygroup", "--theme", "classic", "--format", "png"])
        self.assertEqual(args.command, "analyze")
        self.assertEqual(args.group, "mygroup")
        self.assertEqual(args.theme, "classic")
        self.assertEqual(args.format, "png")

    def test_parse_schedule_create_args(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["schedule", "create", "mygroup", "--hour", "8", "--minute", "30"])
        self.assertEqual(args.command, "schedule")
        self.assertEqual(args.subcmd, "create")
        self.assertEqual(args.group, "mygroup")
        self.assertEqual(args.hour, 8)
        self.assertEqual(args.minute, 30)

    def test_parse_status(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["status"])
        self.assertEqual(args.command, "status")

    def test_parse_health(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["health"])
        self.assertEqual(args.command, "health")

    def test_parse_reports(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["reports"])
        self.assertEqual(args.command, "reports")

    def test_parse_report_delete(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["report-delete", "r1.html"])
        self.assertEqual(args.command, "report-delete")
        self.assertEqual(args.filename, "r1.html")

    def test_parse_groups(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["groups"])
        self.assertEqual(args.command, "groups")

    def test_parse_chatlog_talkers(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["chatlog", "talkers"])
        self.assertEqual(args.command, "chatlog")
        self.assertEqual(args.subcmd, "talkers")

    def test_parse_chatlog_load(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["chatlog", "load", "g1@chatroom", "--limit", "100"])
        self.assertEqual(args.subcmd, "load")
        self.assertEqual(args.talker, "g1@chatroom")
        self.assertEqual(args.limit, 100)

    def test_parse_serve(self):
        svc = CLIService(MagicMock())
        parser = svc._build_parser()
        args = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "9090"])
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9090)


class TestDispatch(unittest.TestCase):
    """测试 _dispatch() 路由"""

    def test_dispatch_no_command(self):
        """无命令时打印帮助"""
        svc = CLIService(MagicMock())
        with patch.object(svc, "_build_parser") as mock_bp:
            mock_parser = MagicMock()
            mock_args = argparse.Namespace(command=None)
            mock_bp.return_value = mock_parser
            svc.run([])

    def test_dispatch_analyze(self):
        ga = _make_ga()
        ga.web.auto_analyze.return_value = {"success": True, "method": "rule", "report": {}}
        svc = CLIService(ga)
        with patch("builtins.print"):
            svc.run(["analyze", "test_group"])

    def test_dispatch_status(self):
        ga = _make_ga()
        ga.web.get_status.return_value = {
            "chatlog_available": True, "chatlog_talkers_count": 0,
            "ai_configured": False, "ai_provider": "", "report_count": 0,
            "report_total_size_kb": 0,
        }
        svc = CLIService(ga)
        with patch("builtins.print"):
            svc.run(["status"])

    def test_dispatch_chatlog_without_subcmd(self):
        ga = _make_ga()
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.run(["chatlog"])
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("请指定 chatlog 子命令", output)

    def test_dispatch_schedule_without_subcmd(self):
        ga = _make_ga()
        svc = CLIService(ga)
        with patch("builtins.print") as mock_print:
            svc.run(["schedule"])
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("请指定 schedule 子命令", output)


class TestMain(unittest.TestCase):
    """测试 main() 入口函数"""

    @patch("chatlens.plugins.cli.commands.CLIService")
    @patch("chatlens.core.PluginRegistry")
    @patch("chatlens.core.GroupAnalysis")
    @patch("chatlens.core.providers.WechatProvider")
    @patch("sys.argv", ["wxcli", "status"])
    def test_main_creates_service_and_runs(self, MockWechatProvider, MockGroupAnalysis,
                                            MockPluginRegistry, MockCLIService):
        mock_ga = MagicMock()
        MockGroupAnalysis.return_value = mock_ga
        mock_svc = MagicMock()
        MockCLIService.return_value = mock_svc
        mock_registry = MagicMock()
        MockPluginRegistry.return_value = mock_registry

        main()

        MockGroupAnalysis.assert_called_once()
        mock_registry.discover.assert_called_once()
        mock_registry.load_all.assert_called_once_with(mock_ga)
        MockCLIService.assert_called_once_with(mock_ga)
        mock_svc.run.assert_called_once()

    @patch("chatlens.plugins.cli.commands.CLIService")
    @patch("chatlens.core.PluginRegistry")
    @patch("chatlens.core.GroupAnalysis")
    @patch("chatlens.core.providers.WechatProvider")
    @patch("sys.argv", ["wxcli", "status"])
    def test_main_with_config_file(self, MockWechatProvider, MockGroupAnalysis,
                                    MockPluginRegistry, MockCLIService):
        """有配置文件时正确加载"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"chatlog": {"api_base": "http://localhost:5030"}}, f)
            cfg_path = f.name

        # 把配置文件放到 chatlens/config/config.json 路径
        config_dir = os.path.join(os.path.dirname(__file__), "chatlens", "config")
        os.makedirs(config_dir, exist_ok=True)
        real_cfg = os.path.join(config_dir, "config.json")

        # 保存原始配置（如果存在）
        original_content = None
        if os.path.exists(real_cfg):
            with open(real_cfg, "r", encoding="utf-8") as f:
                original_content = f.read()

        try:
            import shutil
            shutil.copy2(cfg_path, real_cfg)

            mock_ga = MagicMock()
            MockGroupAnalysis.return_value = mock_ga
            mock_svc = MagicMock()
            MockCLIService.return_value = mock_svc
            mock_registry = MagicMock()
            MockPluginRegistry.return_value = mock_registry

            main()
            MockGroupAnalysis.assert_called_once()
        finally:
            # 恢复原始配置
            if original_content is not None:
                with open(real_cfg, "w", encoding="utf-8") as f:
                    f.write(original_content)
            elif os.path.exists(real_cfg):
                os.unlink(real_cfg)
            os.unlink(cfg_path)


class TestSetup(unittest.TestCase):
    """测试 setup() 插件注册"""

    def test_setup_registers_cli(self):
        from chatlens.plugins.cli.commands import setup
        ga = MagicMock()
        setup(ga)
        self.assertTrue(hasattr(ga, "cli"))
        self.assertIsInstance(ga.cli, CLIService)


if __name__ == "__main__":
    unittest.main()

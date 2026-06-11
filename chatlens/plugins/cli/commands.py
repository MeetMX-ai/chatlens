import argparse
import json
import logging
import os
import sys
from typing import Any, List, Optional, Sequence

from chatlens.core._chatlog_runtime import run_chatlog_decrypt

logger = logging.getLogger("chatlens.plugins.cli")


def _fmt_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    widths: Optional[Sequence[int]] = None,
) -> str:
    if not widths:
        widths = [
            max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
            for i, h in enumerate(headers)
        ]
    header_line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    sep_line = "  ".join("-" * w for w in widths)
    lines = [header_line, sep_line]
    for row in rows:
        lines.append("  ".join(str(v).ljust(w) for v, w in zip(row, widths)))
    return "\n".join(lines)


class CLIService:
    def __init__(self, ga: Any) -> None:
        self.ga = ga

    def cmd_groups(self, args: argparse.Namespace) -> None:
        result = (
            self.ga.web.get_groups()
            if hasattr(self.ga, "web") and self.ga.web
            else {"groups": []}
        )
        if not result.get("groups"):
            print('暂无群聊数据。使用 "wxcli chatlog load <talker>" 加载。')
            return
        rows = []
        for g in result.get("group_info", []):
            name = g.get("label", g.get("value", ""))
            val = g.get("value", "")
            rows.append([name, val])
        print(_fmt_table(["群聊名称", "标识"], rows))

    def cmd_chatlog_talkers(self, args: argparse.Namespace) -> None:
        result = (
            self.ga.web.get_chatlog_talkers()
            if hasattr(self.ga, "web") and self.ga.web
            else {"talkers": []}
        )
        talkers = result.get("talkers", [])
        if not talkers:
            print("chatlog 无可用数据。请先运行 wxcli chatlog decrypt。")
            return
        groups = [t for t in talkers if t.get("talker", "").endswith("@chatroom")]
        rows = []
        for g in groups:
            rows.append(
                [
                    g.get("display_name", g.get("talker", "")),
                    g.get("talker", ""),
                    str(g.get("message_count", 0)),
                ]
            )
        print(f"共 {len(groups)} 个群聊：")
        print(_fmt_table(["名称", "Talker", "消息数"], rows))

    def cmd_chatlog_load(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        result = self.ga.web.load_from_chatlog(args.talker, limit=args.limit or 0)
        if result.get("success"):
            print(f"✅ 已加载 {result.get('message_count', 0)} 条消息 ({args.talker})")
        else:
            print(f"❌ 加载失败: {result.get('error', '未知错误')}")

    def cmd_chatlog_decrypt(self, args: argparse.Namespace) -> None:
        ok = run_chatlog_decrypt()
        if ok:
            print("✅ 数据库解密成功")
        else:
            print("❌ 数据库解密失败，请检查 chatlog 配置")

    def cmd_analyze(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        print(f"正在分析 {args.group} ...")
        result = self.ga.web.auto_analyze(
            args.group,
            theme=args.theme or "scrapbook",
            fmt=args.format or "jpg",
            start_date=args.start_date or "",
            end_date=args.end_date or "",
        )
        if result.get("success"):
            method = result.get("method", "")
            report = result.get("report", {})
            print(f"✅ 分析完成 (方法: {method})")
            if report.get("html_url"):
                print(f"  HTML: {report['html_url']}")
            if report.get("image_url"):
                print(f"  图片: {report['image_url']}")
        else:
            print(f"❌ 分析失败: {result.get('error', '未知错误')}")

    def cmd_schedule_create(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        result = self.ga.web.create_scheduled_task(
            args.group,
            hour=args.hour,
            minute=args.minute,
            theme=args.theme or "scrapbook",
            fmt=args.format or "jpg",
        )
        if result.get("success"):
            task = result.get("task", {})
            print("✅ 定时任务已创建")
            print(f"  任务ID: {result['task_id']}")
            print(f"  群聊: {task.get('group_name')}")
            print(f"  时间: 每天 {task.get('hour', 0):02d}:{task.get('minute', 0):02d}")
        else:
            print(f"❌ 创建失败: {result.get('error', '未知错误')}")

    def cmd_schedule_list(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        result = self.ga.web.list_scheduled_tasks()
        tasks = result.get("tasks", [])
        if not tasks:
            print("暂无定时任务")
            return
        rows = []
        status_map = {
            "idle": "等待",
            "running": "执行中",
            "completed": "完成",
            "failed": "失败",
            "timeout": "超时",
        }
        for t in tasks:
            enabled = "✅" if t.get("enabled", True) else "⏸️"
            status = status_map.get(t.get("status", ""), t.get("status", ""))
            rows.append(
                [
                    t.get("task_id", ""),
                    t.get("group_name", ""),
                    f"{t.get('hour', 0):02d}:{t.get('minute', 0):02d}",
                    enabled,
                    status,
                    t.get("last_run", "-")[:16],
                ]
            )
        print(_fmt_table(["ID", "群聊", "时间", "启用", "状态", "上次执行"], rows))

    def cmd_schedule_trigger(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        result = self.ga.web.trigger_scheduled_task(args.task_id)
        if result.get("success"):
            print(f"✅ 已触发任务 {args.task_id}，后台执行中")
        else:
            print(f"❌ 触发失败: {result.get('error', '未知错误')}")

    def cmd_schedule_delete(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        result = self.ga.web.delete_scheduled_task(args.task_id)
        if result.get("success"):
            print(f"✅ 已删除任务 {args.task_id}")
        else:
            print(f"❌ 删除失败: {result.get('error', '未知错误')}")

    def cmd_schedule_toggle(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        result = self.ga.web.toggle_scheduled_task(args.task_id, args.enabled)
        label = "启用" if args.enabled else "禁用"
        if result.get("success"):
            print(f"✅ 已{label}任务 {args.task_id}")
        else:
            print(f"❌ 操作失败: {result.get('error', '未知错误')}")

    def cmd_reports(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "report") or not self.ga.report:
            print("❌ Report 插件未启用")
            return
        result = self.ga.report.list_reports()
        reports = result.get("reports", [])
        if not reports:
            print("暂无报告")
            return
        rows = []
        for r in reports:
            rows.append(
                [
                    r.get("filename", ""),
                    r.get("size_kb", 0),
                    r.get("created_at", "")[:16],
                ]
            )
        print(_fmt_table(["文件名", "大小(KB)", "创建时间"], rows))

    def cmd_report_delete(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "report") or not self.ga.report:
            print("❌ Report 插件未启用")
            return
        result = self.ga.report.delete_report(args.filename)
        if result.get("success"):
            print(f"✅ 已删除 {args.filename}")
        else:
            print(f"❌ 删除失败: {result.get('error', '未知错误')}")

    def cmd_status(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        result = self.ga.web.get_status()
        print(
            f"chatlog:  {'✅ 已连接' if result.get('chatlog_available') else '❌ 未连接'}"
        )
        print(f"群聊数:   {result.get('chatlog_talkers_count', 0)}")
        print(
            f"AI:       {'✅ ' + result.get('ai_provider', '') if result.get('ai_configured') else '❌ 未配置'}"
        )
        print(f"报告数:   {result.get('report_count', 0)}")
        print(f"报告大小: {result.get('report_total_size_kb', 0)} KB")

    def cmd_health(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        result = self.ga.web.get_health()
        print(f"状态:     {result.get('status')}")
        print(f"运行时间: {result.get('uptime')}")
        print(f"内存:     {result.get('memory_mb', 0):.1f} MB")
        print(f"chatlog:  {'✅' if result.get('chatlog_available') else '❌'}")
        print(f"定时任务: {result.get('scheduled_tasks', 0)} 个")
        errors = result.get("recent_errors", [])
        if errors:
            print("最近错误:")
            for e in errors[:5]:
                print(f"  - {e}")

    def cmd_serve(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "web") or not self.ga.web:
            print("❌ Web 插件未启用")
            return
        from chatlens._defaults import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

        host = args.host or self.ga.config.get("server", {}).get(
            "host", DEFAULT_SERVER_HOST
        )
        port = args.port or self.ga.config.get("server", {}).get(
            "port", DEFAULT_SERVER_PORT
        )
        from chatlens.plugins.web.handler import run_server

        run_server(self.ga, host=host, port=port)

    def cmd_mcp(self, args: argparse.Namespace) -> None:
        if not hasattr(self.ga, "mcp") or not self.ga.mcp:
            print("❌ MCP 插件未启用")
            return
        from chatlens.plugins.mcp.mcp_server import run_server as _mcp_run

        _mcp_run()

    def run(self, argv: Optional[List[str]] = None) -> None:
        parser = self._build_parser()
        args = parser.parse_args(argv)
        if not args.command:
            parser.print_help()
            return
        self._dispatch(args, parser)

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="wxcli", description="微信群聊分析工具 CLI"
        )
        sub = parser.add_subparsers(dest="command", help="可用命令")

        from chatlens._defaults import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

        p_serve = sub.add_parser("serve", help="启动 Web 服务器")
        p_serve.add_argument("--host", default=DEFAULT_SERVER_HOST)
        p_serve.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT)
        p_serve.add_argument("--no-chatlog", action="store_true")
        p_serve.add_argument("--no-decrypt", action="store_true")
        p_serve.add_argument("--debug", action="store_true")

        sub.add_parser("groups", help="列出已加载的群聊")

        p_mcp = sub.add_parser("mcp", help="启动 MCP 服务器")
        p_mcp.add_argument("--transport", default="stdio")

        p_cl = sub.add_parser("chatlog", help="chatlog 数据源操作")
        cl_sub = p_cl.add_subparsers(dest="subcmd")
        cl_sub.add_parser("talkers", help="列出 chatlog 所有聊天对象")
        p_load = cl_sub.add_parser("load", help="从 chatlog 加载群聊消息")
        p_load.add_argument("talker", help="talker 标识 (如 xxx@chatroom)")
        p_load.add_argument(
            "--limit", type=int, default=0, help="消息数量限制 (0=全部)"
        )
        cl_sub.add_parser("decrypt", help="解密微信数据库")

        p_analyze = sub.add_parser("analyze", help="分析群聊并生成报告")
        p_analyze.add_argument("group", help="群聊名称或标识")
        p_analyze.add_argument(
            "--theme", choices=["scrapbook", "classic", "hack"], default="scrapbook"
        )
        p_analyze.add_argument(
            "--format", choices=["jpg", "png", "html"], default="html"
        )
        p_analyze.add_argument(
            "--start-date", dest="start_date", help="开始日期 (YYYY-MM-DD)"
        )
        p_analyze.add_argument(
            "--end-date", dest="end_date", help="结束日期 (YYYY-MM-DD)"
        )

        p_sched = sub.add_parser("schedule", help="定时任务管理")
        sched_sub = p_sched.add_subparsers(dest="subcmd")
        p_sc = sched_sub.add_parser("create", help="创建定时任务")
        p_sc.add_argument("group", help="群聊名称")
        p_sc.add_argument("--hour", type=int, required=True, help="小时 (0-23)")
        p_sc.add_argument("--minute", type=int, default=0, help="分钟 (0-59)")
        p_sc.add_argument(
            "--theme", choices=["scrapbook", "classic", "hack"], default="scrapbook"
        )
        p_sc.add_argument(
            "--format", dest="format", choices=["jpg", "png"], default="jpg"
        )
        sched_sub.add_parser("list", help="列出定时任务")
        p_st = sched_sub.add_parser("trigger", help="手动触发定时任务")
        p_st.add_argument("task_id", help="任务ID")
        p_sd = sched_sub.add_parser("delete", help="删除定时任务")
        p_sd.add_argument("task_id", help="任务ID")
        p_stog = sched_sub.add_parser("toggle", help="启用/禁用定时任务")
        p_stog.add_argument("task_id", help="任务ID")
        p_stog.add_argument(
            "--enable", dest="enabled", action="store_true", default=True
        )
        p_stog.add_argument("--disable", dest="enabled", action="store_false")

        sub.add_parser("reports", help="列出报告文件")
        p_rd = sub.add_parser("report-delete", help="删除报告文件")
        p_rd.add_argument("filename", help="文件名")

        sub.add_parser("status", help="查看系统状态")
        sub.add_parser("health", help="健康检查")

        return parser

    def _dispatch(
        self, args: argparse.Namespace, parser: argparse.ArgumentParser
    ) -> None:
        cmd_map = {
            "serve": self.cmd_serve,
            "mcp": self.cmd_mcp,
            "groups": self.cmd_groups,
            "analyze": self.cmd_analyze,
            "reports": self.cmd_reports,
            "report-delete": self.cmd_report_delete,
            "status": self.cmd_status,
            "health": self.cmd_health,
        }
        if args.command == "chatlog":
            chatlog_map = {
                "talkers": self.cmd_chatlog_talkers,
                "load": self.cmd_chatlog_load,
                "decrypt": self.cmd_chatlog_decrypt,
            }
            fn = chatlog_map.get(args.subcmd)
            if fn:
                fn(args)
            else:
                print("请指定 chatlog 子命令: talkers / load <talker> / decrypt")
        elif args.command == "schedule":
            schedule_map = {
                "create": self.cmd_schedule_create,
                "list": self.cmd_schedule_list,
                "trigger": self.cmd_schedule_trigger,
                "delete": self.cmd_schedule_delete,
                "toggle": self.cmd_schedule_toggle,
            }
            fn = schedule_map.get(args.subcmd)
            if fn:
                fn(args)
            else:
                print(
                    "请指定 schedule 子命令: create / list / trigger / delete / toggle"
                )
        else:
            fn = cmd_map.get(args.command)
            if fn:
                fn(args)
            else:
                parser.print_help()


def main():
    from chatlens.core import GroupAnalysis, PluginRegistry
    from chatlens.core.providers import WechatProvider
    from chatlens.logging_config import setup_logging

    config: dict = {}
    try:
        cfg_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "config.json"
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                config = json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        pass

    log_level = config.get("logging", {}).get("level", "INFO")
    log_file = config.get("logging", {}).get("file")
    setup_logging(level=log_level, log_file=log_file)
    chatlog_cfg = config.get("chatlog", {})
    from chatlens._defaults import DEFAULT_CHATLOG_API_BASE

    providers = [
        WechatProvider(
            api_base=chatlog_cfg.get("api_base", DEFAULT_CHATLOG_API_BASE),
            db_path=chatlog_cfg.get("db_path"),
        )
    ]
    ga = GroupAnalysis(config, providers=providers)
    r = PluginRegistry()
    r.discover()
    r.load_all(ga)
    service = CLIService(ga)
    service.run(sys.argv[1:])


def setup(ga):
    service = CLIService(ga)
    ga.cli = service
    logger.info("CLI 插件已注册")


if __name__ == "__main__":
    main()

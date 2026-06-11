"""iLink Bot 指令解析与处理"""

import logging
from typing import Any, Callable

logger = logging.getLogger("chatlens.plugins.ilink")


class CommandHandler:
    """处理微信私聊指令"""

    PREFIX = "/"

    def __init__(
        self,
        ga: Any,
        send_func: Callable[[str, str], bool],
        typing_func: Callable[[str], None],
    ):
        self.ga = ga
        self.send = send_func
        self.typing = typing_func
        self._commands = {
            "帮助": self._cmd_help,
            "群列表": self._cmd_groups,
            "统计": self._cmd_stats,
            "分析": self._cmd_analyze,
            "日报": self._cmd_daily,
            "定时": self._cmd_schedule,
            "状态": self._cmd_status,
        }

    def _get_server_url(self) -> str:
        """获取当前服务器地址（避免硬编码端口）"""
        from chatlens._defaults import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

        host = self.ga.config.get("server", {}).get("host", DEFAULT_SERVER_HOST)
        port = int(self.ga.config.get("server", {}).get("port", DEFAULT_SERVER_PORT))
        return f"http://{host}:{port}"

    def handle(self, text: str, from_user_id: str) -> None:
        """解析并处理用户指令"""
        text = text.strip()
        if not text.startswith(self.PREFIX):
            return

        parts = text[len(self.PREFIX) :].split(None, 1)
        if not parts:
            self.send("未知指令，发送 /帮助 查看可用指令", from_user_id)
            return

        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        handler = self._commands.get(cmd)
        if handler:
            try:
                handler(from_user_id, args)
            except Exception as e:
                logger.error(f"处理指令 /{cmd} 失败: {e}")
                self.send(f"处理指令时出错: {e}", from_user_id)
        else:
            self.send(
                f"未知指令: /{cmd}\n发送 /帮助 查看可用指令",
                from_user_id,
            )

    def _cmd_help(self, user_id: str, args: str) -> None:
        """显示帮助信息"""
        help_text = (
            "ChatLens 微信助手 — 可用指令:\n\n"
            "/帮助 — 显示本帮助\n"
            "/群列表 — 查看已加载的群聊\n"
            "/统计 群名 — 查看群聊统计摘要\n"
            "/分析 群名 — AI 智能分析群聊\n"
            "/日报 群名 日期 — 查看指定日期日报\n"
            "  日期格式: 2026-06-01\n"
            "/定时 群名 HH:MM — 创建每日定时分析\n"
            "  例: /定时 技术交流群 09:00\n"
            "/状态 — 查看系统状态"
        )
        self.send(help_text, user_id)

    def _cmd_groups(self, user_id: str, args: str) -> None:
        """列出群聊"""
        groups = self.ga.get_groups()
        if not groups:
            self.send("暂无已加载的群聊数据", user_id)
            return

        # 尝试获取显示名
        display_names = []
        available = self.ga.providers.get_available()
        for g in groups:
            display = g
            for p in available:
                try:
                    dn = p.get_display_name(g)
                    if dn and dn != g:
                        display = dn
                        break
                except Exception:
                    logger.debug(f"获取 {g} 的显示名称失败", exc_info=True)
            display_names.append(display)

        lines = [f"已加载 {len(groups)} 个群聊:"]
        for i, name in enumerate(display_names[:20], 1):
            lines.append(f"  {i}. {name}")
        if len(display_names) > 20:
            lines.append(f"  ... 还有 {len(display_names) - 20} 个")
        self.send("\n".join(lines), user_id)

    def _cmd_stats(self, user_id: str, args: str) -> None:
        """统计摘要"""
        group_name = args.strip()
        if not group_name:
            self.send("用法: /统计 群名", user_id)
            return

        messages = self.ga.get_messages(group_name)
        if not messages:
            self.send(f"未找到「{group_name}」的数据，请确认群名正确", user_id)
            return

        self.typing(user_id)
        result = self.ga.stats_analyzer.analyze(messages)
        ov = result.get("overview", {})
        # overview 实际键: total_messages, total_members, time_range.start/end, avg_messages_per_day
        tr = ov.get("time_range", {})
        start = tr.get("start", "")[:10] if tr.get("start") else ""
        end = tr.get("end", "")[:10] if tr.get("end") else ""
        avg = ov.get("avg_messages_per_day", 0)
        text = (
            f"「{group_name}」统计摘要\n\n"
            f"消息总数: {ov.get('total_messages', 0)}\n"
            f"参与成员: {ov.get('total_members', 0)}\n"
            f"时间范围: {start} ~ {end}\n"
            f"日均消息: {avg:.0f}"
        )
        top = result.get("member_stats", [])[:5]
        if top:
            text += "\n\n活跃 Top 5:"
            for i, m in enumerate(top, 1):
                name = m.get("sender", m.get("name", ""))
                count = m.get("message_count", m.get("count", 0))
                text += f"\n  {i}. {name} ({count}条)"
        self.send(text, user_id)

    def _cmd_analyze(self, user_id: str, args: str) -> None:
        """AI 智能分析"""
        group_name = args.strip()
        if not group_name:
            self.send("用法: /分析 群名", user_id)
            return

        messages = self.ga.get_messages(group_name)
        if not messages:
            self.send(f"未找到「{group_name}」的数据，请确认群名正确", user_id)
            return

        self.typing(user_id)
        self.send(f"正在分析「{group_name}」，请稍候...", user_id)

        if not hasattr(self.ga, "web") or not self.ga.web:
            self.send("Web 服务未启用，无法执行分析", user_id)
            return

        result = self.ga.web.auto_analyze(group_name)
        if not result.get("success"):
            self.send(f"分析失败: {result.get('error', '未知错误')}", user_id)
            return

        ai_data = result.get("data", {})
        method = result.get("method", "")
        text = f"「{group_name}」分析结果 (方法: {method})\n\n"

        summary = ai_data.get("summary", {}).get("summary", "")
        if summary:
            text += f"摘要: {summary}\n\n"

        # user_titles 实际键: name, title, mbti, sbti, acgti, reason
        titles = ai_data.get("user_titles", {}).get("user_titles", [])
        if titles:
            text += "用户称号:\n"
            for t in titles[:5]:
                text += f"  {t.get('name', '')}: {t.get('title', '')}\n"
            text += "\n"

        # golden_quotes 实际键: content, sender, reason
        quotes = ai_data.get("golden_quotes", {}).get("golden_quotes", [])
        if quotes:
            text += "金句:\n"
            for q in quotes[:3]:
                text += f"  「{q.get('content', '')}」— {q.get('sender', '')}\n"
            text += "\n"

        # chat_quality 实际键: title, subtitle, dimensions, summary
        cq = ai_data.get("chat_quality", {})
        quality_text = cq.get("summary", "") or cq.get("subtitle", "")
        if quality_text:
            text += f"质量锐评: {quality_text}"

        report = result.get("report", {})
        if report.get("image_url"):
            text += f"\n\n报告已生成: {self._get_server_url()}{report['image_url']}"

        self.send(text, user_id)

    def _cmd_daily(self, user_id: str, args: str) -> None:
        """日报分析"""
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            self.send("用法: /日报 群名 日期\n例: /日报 技术交流群 2026-06-01", user_id)
            return

        group_name = parts[0]
        date = parts[1]

        messages = self.ga.get_messages(group_name)
        if not messages:
            self.send(f"未找到「{group_name}」的数据", user_id)
            return

        self.typing(user_id)
        if not hasattr(self.ga, "web") or not self.ga.web:
            self.send("Web 服务未启用", user_id)
            return

        result = self.ga.web.daily_auto_report(group_name, date)
        if not result.get("success"):
            self.send(f"日报生成失败: {result.get('error', '未知错误')}", user_id)
            return

        text = (
            f"「{group_name}」{date} 日报\n\n消息数: {result.get('message_count', 0)}"
        )
        report = result.get("report", {})
        if report.get("image_url"):
            text += f"\n\n报告: {self._get_server_url()}{report['image_url']}"
        self.send(text, user_id)

    def _cmd_schedule(self, user_id: str, args: str) -> None:
        """创建定时任务"""
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            self.send("用法: /定时 群名 HH:MM\n例: /定时 技术交流群 09:00", user_id)
            return

        group_name = parts[0]
        time_str = parts[1]
        try:
            time_parts = time_str.split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1]) if len(time_parts) > 1 else 0
        except (ValueError, IndexError):
            self.send("时间格式错误，请使用 HH:MM 格式", user_id)
            return

        if not hasattr(self.ga, "schedule") or not self.ga.schedule:
            self.send("定时任务插件未启用", user_id)
            return

        result = self.ga.schedule.create(group_name, hour, minute)
        if result.get("success"):
            self.send(
                f"已创建定时任务: 每天 {hour:02d}:{minute:02d} 分析「{group_name}」",
                user_id,
            )
        else:
            self.send(f"创建失败: {result.get('error', '未知错误')}", user_id)

    def _cmd_status(self, user_id: str, args: str) -> None:
        """系统状态"""
        groups = self.ga.get_groups()
        has_api = self.ga.has_api_key()
        wechat = self.ga.get_provider("wechat")
        chatlog_ok = wechat.is_available() if wechat else False
        ilink_ok = (
            hasattr(self.ga, "ilink") and self.ga.ilink and self.ga.ilink.is_connected()
        )

        text = (
            "ChatLens 系统状态\n\n"
            f"已加载群聊: {len(groups)}\n"
            f"AI 分析: {'已配置' if has_api else '未配置'}\n"
            f"微信数据源: {'可用' if chatlog_ok else '不可用'}\n"
            f"iLink Bot: {'已连接' if ilink_ok else '未连接'}"
        )
        if hasattr(self.ga, "schedule") and self.ga.schedule:
            tasks = self.ga.schedule.list_all()
            task_count = len(tasks.get("tasks", []))
            text += f"\n定时任务: {task_count} 个"
        self.send(text, user_id)

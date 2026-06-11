import json
import os
import logging
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

logger = logging.getLogger("chatlens.plugins.mcp")

mcp = FastMCP("微信群聊分析")

_service: Optional["MCPService"] = None


def get_service() -> Optional["MCPService"]:
    """获取当前 MCPService 实例，方便测试时 mock"""
    return _service


def set_service(service: Optional["MCPService"]) -> None:
    """设置 MCPService 实例，方便测试时替换"""
    global _service
    _service = service


# ─── Pydantic 结构化输出模型 ──────────────────────────────────────────────────


class AnalysisResult(BaseModel):
    """群聊分析结构化结果"""

    group_name: str = Field(description="群聊名称")
    total_messages: int = Field(default=0, description="消息总数")
    total_members: int = Field(default=0, description="活跃成员数")
    avg_messages_per_day: float = Field(default=0, description="日均消息数")
    time_range_start: str = Field(default="", description="起始时间")
    time_range_end: str = Field(default="", description="结束时间")
    top_members: list = Field(default_factory=list, description="活跃成员列表")
    markdown: str = Field(default="", description="Markdown 格式分析报告")


class AIAnalysisResult(BaseModel):
    """AI 智能分析结构化结果"""

    group_name: str = Field(description="群聊名称")
    analysis_type: str = Field(default="full", description="分析类型")
    method: str = Field(default="rule", description="分析方法: ai 或 rule")
    data: dict = Field(default_factory=dict, description="分析数据")
    markdown: str = Field(default="", description="Markdown 格式分析结果")


# ─── 输入验证 ────────────────────────────────────────────────────────────────


def _validate_group_name(name: str) -> str:
    """验证群名，防止路径遍历"""
    if not name or not name.strip():
        raise ValueError("群名不能为空")
    name = name.strip()
    # 防止路径遍历
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"群名包含非法字符: {name}")
    if len(name) > 200:
        raise ValueError("群名过长（最大 200 字符）")
    return name


def _validate_keyword(keyword: str) -> str:
    """验证搜索关键词"""
    if not keyword or not keyword.strip():
        raise ValueError("关键词不能为空")
    keyword = keyword.strip()
    if len(keyword) > 500:
        raise ValueError("关键词过长（最大 500 字符）")
    return keyword


def _validate_time(hour: int, minute: int) -> None:
    """验证时间参数"""
    if not 0 <= hour <= 23:
        raise ValueError(f"hour 必须在 0-23 之间，当前: {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"minute 必须在 0-59 之间，当前: {minute}")


# ─── MCPService ──────────────────────────────────────────────────────────────


class MCPService:
    def __init__(self, ga: Any) -> None:
        self.ga = ga

    def get_messages(self, group_name: str):
        if self.ga.has_messages(group_name):
            return self.ga.get_messages(group_name)
        return self.ga.load_from_file(group_name)

    def get_stats(self, group_name: str):
        return self.ga.stats_analyzer.analyze(self.get_messages(group_name))

    def get_chatlog(self):
        """向后兼容 — 返回微信 provider 的 bridge"""
        p = self.ga.get_provider("wechat")
        return p.bridge if p else None

    def get_provider(self, name="wechat"):
        return self.ga.get_provider(name)

    def get_ai(self):
        return self.ga.ai_analyzer

    def get_collector(self):
        return self

    def get_data_files(self):
        return self.ga.get_data_files()


def _fmt_msg(m) -> str:
    if m.msg_attr == "system":
        return ""
    sender = m.sender_remark or m.sender or "未知"
    time_part = ""
    if m.timestamp:
        try:
            time_part = m.timestamp.split(" ")[1][:5]
        except (ValueError, IndexError):
            pass
    if m.msg_type == "text":
        return f"[{time_part}] {sender}: {m.content}"
    if m.msg_type == "image":
        return f"[{time_part}] {sender}: [图片]"
    if m.msg_type == "voice":
        return f"[{time_part}] {sender}: [语音]"
    if m.msg_type == "quote":
        return f"[{time_part}] {sender}: (引用) {m.quote_content[:80]} | {m.content}"
    if m.msg_type == "emotion":
        return f"[{time_part}] {sender}: [表情]"
    return f"[{time_part}] {sender}: [{m.msg_type}]"


def _s():
    return get_service()


def _get_server_url() -> str:
    """从配置中读取服务器地址，回退到默认值"""
    from chatlens._defaults import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

    s = _s()
    if s and s.ga and s.ga.config:
        host = s.ga.config.get("server", {}).get("host", DEFAULT_SERVER_HOST)
        port = int(s.ga.config.get("server", {}).get("port", DEFAULT_SERVER_PORT))
        return f"http://{host}:{port}"
    return f"http://{DEFAULT_SERVER_HOST}:{DEFAULT_SERVER_PORT}"


def _http_post(path: str, data: dict) -> dict:
    url = f"{_get_server_url()}{path}"
    try:
        resp = httpx.post(url, json=data, timeout=10)
        return resp.json()  # type: ignore[no-any-return]
    except (httpx.HTTPError, json.JSONDecodeError, OSError) as e:
        return {"success": False, "error": str(e)}


def _http_get(path: str) -> dict:
    url = f"{_get_server_url()}{path}"
    try:
        resp = httpx.get(url, timeout=10)
        return resp.json()  # type: ignore[no-any-return]
    except (httpx.HTTPError, json.JSONDecodeError, OSError) as e:
        return {"success": False, "error": str(e)}


def _http_delete(path: str, data: dict) -> dict:
    url = f"{_get_server_url()}{path}"
    try:
        resp = httpx.delete(url, json=data, timeout=10)  # type: ignore[call-arg]
        return resp.json()  # type: ignore[no-any-return]
    except (httpx.HTTPError, json.JSONDecodeError, OSError) as e:
        return {"success": False, "error": str(e)}


def _ai_or_rule(messages, ai_method_name: str, result_key: str):
    """统一的 AI 调用 + 规则分析回退逻辑。

    Args:
        messages: 消息列表
        ai_method_name: AI 分析器上的方法名（如 'analyze_user_titles'）
        result_key: 结果字典中的键名（如 'user_titles'）

    Returns:
        (result_dict, used_fallback): 结果字典和是否使用了规则回退
    """
    from chatlens.core.ai_analyzer import rule_based_analysis

    s = _s()
    ai = s.get_ai()
    _used_fallback = False
    if ai.api_key:
        try:
            method = getattr(ai, ai_method_name)
            result = method(messages)
            return result, False
        except Exception:
            logger.warning(
                f"AI 方法 {ai_method_name} 调用失败，回退到规则分析", exc_info=True
            )
    result = rule_based_analysis(messages).get(result_key, {})
    return result, True


# ─── P0: 工具定义（带 chatlens_ 前缀 + ToolAnnotations） ────────────────────


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_list_groups(offset: int = 0, limit: int = 20) -> str:
    """列出所有已加载的群聊数据。返回每个群的名称和消息数量。

    Args:
        offset: 起始偏移量，默认 0
        limit: 返回数量上限，默认 20
    """
    s = _s()
    files = s.get_data_files() if s else []
    if not files:
        return "当前没有已加载的群聊数据。请先使用 chatlens_load_data 加载数据。"
    total_count = len(files)
    sliced = files[offset : offset + limit]
    has_more = (offset + limit) < total_count
    lines = [
        f"已加载的群聊数据（共 {total_count} 个，显示 {offset + 1}-{offset + len(sliced)}）：\n"
    ]
    for f in sliced:
        lines.append(
            f"  • {f['group_name']} ({f['message_count']} 条消息, 加载时间: {f.get('collected_at', '未知')})"
        )
    if has_more:
        lines.append(
            f"\n  ... 还有 {total_count - offset - limit} 个，可增大 offset 查看"
        )
    lines.append(f"\ntotal_count: {total_count}, has_more: {has_more}")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_list_talkers(
    talker_type: str = "all", offset: int = 0, limit: int = 20
) -> str:
    """列出 chatlog 数据库中所有可用的聊天对象（群聊/私聊）。

    Args:
        talker_type: 筛选类型 - "all"（全部）、"group"（仅群聊）、"private"（仅私聊），默认 "all"
        offset: 起始偏移量，默认 0
        limit: 返回数量上限，默认 20
    """
    s = _s()
    if not s:
        return "服务未初始化"
    provider = s.get_provider("wechat")
    if not provider or not provider.is_available():
        return "chatlog 数据库不可用。请确认 chatlog_alpha 目录中有解密后的数据库文件。"
    cl = provider.bridge
    talkers = cl.get_all_talkers()
    if talker_type == "group":
        talkers = [t for t in talkers if t["is_chatroom"]]
    elif talker_type == "private":
        talkers = [t for t in talkers if not t["is_chatroom"]]
    if not talkers:
        return "没有找到符合条件的聊天对象。"
    total_count = len(talkers)
    sliced = talkers[offset : offset + limit]
    has_more = (offset + limit) < total_count
    lines = [
        f"共 {total_count} 个聊天对象（显示 {offset + 1}-{offset + len(sliced)}）：\n"
    ]
    for t in sliced:
        tag = "[群]" if t["is_chatroom"] else "[私]"
        name = t.get("display_name") or t["talker"]
        lines.append(f"  {tag} {name} ({t['message_count']} 条) talker={t['talker']}")
    if has_more:
        lines.append(
            f"\n  ... 还有 {total_count - offset - limit} 个，可增大 offset 查看"
        )
    lines.append(f"\ntotal_count: {total_count}, has_more: {has_more}")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True, readOnlyHint=False))
def chatlens_load_data(talker: str, limit: int = 0) -> str:
    """从 chatlog 数据库加载指定聊天对象的消息数据。

    Args:
        talker: 聊天对象标识（如群聊的 xxx@chatroom），可通过 chatlens_list_talkers 查看
        limit: 加载消息数量限制，0 表示加载全部
    """
    try:
        talker = _validate_group_name(talker)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    provider = s.get_provider("wechat")
    if not provider or not provider.is_available():
        return "chatlog 数据库不可用。"
    messages = provider.get_messages(talker, limit)
    if not messages:
        return f"未找到 {talker} 的消息数据。"
    s.ga.set_messages(talker, messages)
    s.ga.save_loaded(talker, messages)
    display_name = provider.get_display_name(talker)
    return f"✅ 已加载 {display_name} ({talker}) 的 {len(messages)} 条消息。现在可以使用 chatlens_analyze_group 或 chatlens_get_messages_for_ai 等工具进行分析。"


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    structured_output=True,
)
def chatlens_analyze_group(group_name: str) -> AnalysisResult:
    """对指定群聊进行统计分析，返回消息概览、成员排名、时间分布等数据。

    Args:
        group_name: 群聊名称或 talker 标识
    """
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return AnalysisResult(group_name=group_name, markdown=f"参数错误: {e}")
    s = _s()
    if not s:
        return AnalysisResult(group_name=group_name, markdown="服务未初始化")
    messages = s.get_messages(group_name)
    if not messages:
        return AnalysisResult(
            group_name=group_name,
            markdown=f"未找到 {group_name} 的消息数据。请先使用 chatlens_load_data 加载。",
        )
    result = s.get_stats(group_name)
    ov = result.get("overview", {})
    lines = [
        f"📊 {group_name} 群聊统计分析\n",
        f"消息总数: {ov.get('total_messages', 0)}",
        f"活跃成员: {ov.get('total_members', 0)}",
        f"日均消息: {ov.get('avg_messages_per_day', 0)}",
    ]
    tr = ov.get("time_range", {})
    if tr.get("start"):
        lines.append(f"时间范围: {tr['start']} ~ {tr['end']}")
    members = result.get("member_stats", [])[:10]
    if members:
        lines.append("\n🏆 活跃成员 TOP 10:")
        for i, m in enumerate(members, 1):
            lines.append(
                f"  {i}. {m['sender']} - {m['msg_count']} 条 ({m['msg_percentage']}%)"
            )
    types = result.get("msg_type_distribution", [])
    if types:
        lines.append("\n📋 消息类型分布:")
        for t in types[:6]:
            lines.append(
                f"  {t.get('label', t['type'])}: {t['count']} ({t['percentage']}%)"
            )
    hourly = result.get("hourly_distribution", [])
    peak_hours = sorted(hourly, key=lambda x: x["count"], reverse=True)[:5]
    if peak_hours and peak_hours[0]["count"] > 0:
        lines.append("\n⏰ 最活跃时段:")
        for h in peak_hours:
            lines.append(f"  {h['label']}: {h['count']} 条")
    keywords = result.get("keyword_cloud", [])[:15]
    if keywords:
        lines.append("\n🔑 高频词:")
        lines.append("  " + "、".join(k["word"] for k in keywords))
    markdown_text = "\n".join(lines)
    return AnalysisResult(
        group_name=group_name,
        total_messages=ov.get("total_messages", 0),
        total_members=ov.get("total_members", 0),
        avg_messages_per_day=ov.get("avg_messages_per_day", 0),
        time_range_start=tr.get("start", ""),
        time_range_end=tr.get("end", ""),
        top_members=members,
        markdown=markdown_text,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_get_messages_for_ai(
    group_name: str, count: int = 200, offset: int = 0
) -> str:
    """获取群聊消息的格式化文本，供你（IDE 的 AI）自行进行智能分析。这是推荐的分析方式。

    Args:
        group_name: 群聊名称或 talker 标识
        count: 获取消息数量，默认 200 条（从最近的消息开始）
        offset: 从最近消息往前的偏移量，默认 0
    """
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    messages = s.get_messages(group_name)
    if not messages:
        return f"未找到 {group_name} 的消息数据。请先使用 chatlens_load_data 加载。"
    provider = s.get_provider("wechat")
    display_name = provider.get_display_name(group_name) if provider else group_name
    end_idx = len(messages) - offset if offset else len(messages)
    start_idx = max(0, end_idx - count)
    recent = messages[start_idx:end_idx]
    lines = [f"📝 {display_name} 最近 {len(recent)} 条消息（offset={offset}）\n"]
    for m in recent:
        fmt = _fmt_msg(m)
        if fmt:
            lines.append(fmt)
    lines.append(f"\n--- 共 {len(recent)} 条消息 ---")
    lines.append(
        "\n💡 请你根据以上消息内容进行分析，生成以下内容后调用 chatlens_generate_report_image："
    )
    lines.append("  1. summary: 群聊整体摘要（2-3句话）")
    lines.append("  2. topics: 主要讨论话题（每个含 name 和 description）")
    lines.append("  3. user_titles: 用户称号列表（每个含 name, title, mbti, reason）")
    lines.append("  4. golden_quotes: 金句列表（每个含 content, sender, reason）")
    lines.append(
        "  5. chat_quality: 质量锐评（含 title, subtitle, dimensions 列表, summary）"
    )
    lines.append("  6. keywords: 关键词列表（每个含 word, relevance 1-10）")
    return "\n".join(lines)


def _parse_ai_data_json(json_str: str) -> dict:
    """解析 JSON 字符串格式的 AI 分析数据，补充 SBTI/ACGTI 映射和默认颜色。"""
    if not json_str or json_str == "{}":
        return {}
    try:
        parsed = json.loads(json_str) if isinstance(json_str, str) else json_str
    except (json.JSONDecodeError, TypeError):
        return {}

    if not parsed:
        return parsed  # type: ignore[no-any-return]

    # 补充 SBTI/ACGTI 映射
    if "user_titles" in parsed:
        try:
            from chatlens.core.ai_analyzer import SBTI_MAP, ACGTI_MAP

            titles = parsed["user_titles"]
            if isinstance(titles, dict):
                titles = titles.get("user_titles", [])
            for t in titles:
                mbti = t.get("mbti", "").upper()
                t.setdefault("sbti", SBTI_MAP.get(mbti, "未知生物"))
                t.setdefault("acgti", ACGTI_MAP.get(mbti, "未知角色"))
            parsed["user_titles"] = {"user_titles": titles}
        except (ImportError, AttributeError):
            pass

    # 补充默认颜色
    if "chat_quality" in parsed:
        quality = parsed["chat_quality"]
        if isinstance(quality, dict):
            default_colors = [
                "#e07850",
                "#d4a853",
                "#4a9b8c",
                "#8b6bb3",
                "#5b8cd0",
                "#ec4899",
            ]
            for i, d in enumerate(quality.get("dimensions", [])):
                if "color" not in d or not d["color"]:
                    d["color"] = default_colors[i % len(default_colors)]

    return parsed  # type: ignore[no-any-return]


def _parse_ai_data_legacy(
    *,
    ai_summary: str = "",
    ai_topics: str = "",
    ai_user_titles: str = "",
    ai_golden_quotes: str = "",
    ai_chat_quality: str = "",
    ai_keywords: str = "",
) -> dict:
    """解析旧式关键字参数格式的 AI 分析数据（向后兼容）。"""
    parsed = {}
    if ai_summary:
        parsed["summary"] = {"summary": ai_summary, "topics": []}
    if ai_topics:
        try:
            topics = json.loads(ai_topics) if isinstance(ai_topics, str) else ai_topics
            parsed.setdefault("summary", {})["topics"] = topics
        except (json.JSONDecodeError, TypeError):
            pass
    if ai_user_titles:
        try:
            titles = (
                json.loads(ai_user_titles)
                if isinstance(ai_user_titles, str)
                else ai_user_titles
            )
            from chatlens.core.ai_analyzer import SBTI_MAP, ACGTI_MAP

            for t in titles:
                mbti = t.get("mbti", "").upper()
                t["sbti"] = SBTI_MAP.get(mbti, "未知生物")
                t["acgti"] = ACGTI_MAP.get(mbti, "未知角色")
            parsed["user_titles"] = {"user_titles": titles}
        except (json.JSONDecodeError, TypeError):
            pass
    if ai_golden_quotes:
        try:
            quotes = (
                json.loads(ai_golden_quotes)
                if isinstance(ai_golden_quotes, str)
                else ai_golden_quotes
            )
            parsed["golden_quotes"] = {"golden_quotes": quotes}
        except (json.JSONDecodeError, TypeError):
            pass
    if ai_chat_quality:
        try:
            quality = (
                json.loads(ai_chat_quality)
                if isinstance(ai_chat_quality, str)
                else ai_chat_quality
            )
            default_colors = [
                "#e07850",
                "#d4a853",
                "#4a9b8c",
                "#8b6bb3",
                "#5b8cd0",
                "#ec4899",
            ]
            for i, d in enumerate(quality.get("dimensions", [])):
                if "color" not in d or not d["color"]:
                    d["color"] = default_colors[i % len(default_colors)]
            parsed["chat_quality"] = quality
        except (json.JSONDecodeError, TypeError):
            pass
    if ai_keywords:
        try:
            keywords = (
                json.loads(ai_keywords) if isinstance(ai_keywords, str) else ai_keywords
            )
            parsed["keywords"] = {"keywords": keywords}
        except (json.JSONDecodeError, TypeError):
            pass
    return parsed


def _parse_ai_data(
    ai_data: str = "{}",
    ai_summary: str = "",
    ai_topics: str = "",
    ai_user_titles: str = "",
    ai_golden_quotes: str = "",
    ai_chat_quality: str = "",
    ai_keywords: str = "",
) -> dict:
    """解析 AI 分析数据。优先使用 ai_data（JSON 字符串），向后兼容旧的 6 个独立参数。"""
    parsed = _parse_ai_data_json(ai_data)
    if not parsed:
        parsed = _parse_ai_data_legacy(
            ai_summary=ai_summary,
            ai_topics=ai_topics,
            ai_user_titles=ai_user_titles,
            ai_golden_quotes=ai_golden_quotes,
            ai_chat_quality=ai_chat_quality,
            ai_keywords=ai_keywords,
        )
    return parsed


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
def chatlens_generate_report_image(
    group_name: str,
    theme: str = "scrapbook",
    fmt: str = "png",
    ai_data: str = "{}",
    ai_summary: str = "",
    ai_topics: str = "",
    ai_user_titles: str = "",
    ai_golden_quotes: str = "",
    ai_chat_quality: str = "",
    ai_keywords: str = "",
) -> str:
    """生成群聊分析报告图片（PNG 或 JPG）。

    Args:
        group_name: 群聊名称或 talker 标识
        theme: 报告主题，默认 "scrapbook"
        fmt: 图片格式，"png" 或 "jpg"，默认 "png"
        ai_data: AI 分析数据 JSON 字符串，格式示例：
            {"summary": "群聊摘要文本", "topics": [{"name": "话题", "description": "描述"}],
             "user_titles": [{"name": "用户", "title": "称号", "mbti": "INTJ", "reason": "理由"}],
             "golden_quotes": [{"content": "金句", "sender": "发送者", "reason": "推荐理由"}],
             "chat_quality": {"title": "锐评标题", "subtitle": "副标题", "dimensions": [...], "summary": "总结"},
             "keywords": [{"word": "关键词", "relevance": 8}]}
        ai_summary ~ ai_keywords: 向后兼容的独立参数（ai_data 优先）
    """
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    messages = s.get_messages(group_name)
    if not messages:
        return f"未找到 {group_name} 的消息数据。请先使用 chatlens_load_data 加载。"
    stats_result = s.get_stats(group_name)
    provider = s.get_provider("wechat")
    display_name = provider.get_display_name(group_name) if provider else group_name
    parsed_ai_data = _parse_ai_data(
        ai_data=ai_data,
        ai_summary=ai_summary,
        ai_topics=ai_topics,
        ai_user_titles=ai_user_titles,
        ai_golden_quotes=ai_golden_quotes,
        ai_chat_quality=ai_chat_quality,
        ai_keywords=ai_keywords,
    )
    try:
        from chatlens.plugins.report.image_report import (
            generate_report_image as _gen_img,
        )

        img_path, html_path = _gen_img(
            group_name=display_name,
            stats=stats_result,
            ai_data=parsed_ai_data,
            theme=theme,
            fmt=fmt,
            generate_image=True,
        )
        if img_path and os.path.exists(img_path):
            return f"✅ 报告图片已生成: {img_path}\n\nHTML 版本: {html_path}\n\n你可以直接打开图片查看，或在资源管理器中找到该文件。"
        elif html_path:
            return f"⚠️ 图片渲染失败，已生成 HTML 版本: {html_path}\n\n请在浏览器中打开 HTML 文件查看。"
        return "❌ 报告生成失败，请检查日志。"
    except (OSError, ValueError, RuntimeError, ImportError) as e:
        logger.error(f"生成报告图片失败: {e}")
        return f"❌ 生成报告图片失败: {e}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
def chatlens_generate_report_pdf(
    group_name: str,
    theme: str = "scrapbook",
    ai_data: str = "{}",
    ai_summary: str = "",
    ai_topics: str = "",
    ai_user_titles: str = "",
    ai_golden_quotes: str = "",
    ai_chat_quality: str = "",
    ai_keywords: str = "",
) -> str:
    """生成群聊分析报告 PDF。

    Args:
        group_name: 群聊名称或 talker 标识
        theme: 报告主题，默认 "scrapbook"
        ai_data: AI 分析数据 JSON 字符串，格式示例：
            {"summary": "群聊摘要文本", "topics": [{"name": "话题", "description": "描述"}],
             "user_titles": [{"name": "用户", "title": "称号", "mbti": "INTJ", "reason": "理由"}],
             "golden_quotes": [{"content": "金句", "sender": "发送者", "reason": "推荐理由"}],
             "chat_quality": {"title": "锐评标题", "subtitle": "副标题", "dimensions": [...], "summary": "总结"},
             "keywords": [{"word": "关键词", "relevance": 8}]}
        ai_summary ~ ai_keywords: 向后兼容的独立参数（ai_data 优先）
    """
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    messages = s.get_messages(group_name)
    if not messages:
        return f"未找到 {group_name} 的消息数据。请先使用 chatlens_load_data 加载。"
    stats_result = s.get_stats(group_name)
    provider = s.get_provider("wechat")
    display_name = provider.get_display_name(group_name) if provider else group_name
    parsed_ai_data = _parse_ai_data(
        ai_data=ai_data,
        ai_summary=ai_summary,
        ai_topics=ai_topics,
        ai_user_titles=ai_user_titles,
        ai_golden_quotes=ai_golden_quotes,
        ai_chat_quality=ai_chat_quality,
        ai_keywords=ai_keywords,
    )
    try:
        from chatlens.plugins.report.pdf_report import generate_report_pdf as _gen_pdf

        pdf_path, html_path = _gen_pdf(
            group_name=display_name,
            stats=stats_result,
            ai_data=parsed_ai_data,
            theme=theme,
        )
        if pdf_path and os.path.exists(pdf_path):
            return f"✅ PDF 报告已生成: {pdf_path}\n\nHTML 版本: {html_path}\n\n你可以直接打开 PDF 查看完整报告。"
        elif html_path:
            return f"⚠️ PDF 生成失败，已生成 HTML 版本: {html_path}\n\n请在浏览器中打开 HTML 文件查看。"
        return "❌ 报告生成失败，请检查日志。"
    except (OSError, ValueError, RuntimeError, ImportError) as e:
        logger.error(f"生成 PDF 报告失败: {e}")
        return f"❌ 生成 PDF 报告失败: {e}"


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    structured_output=True,
)
def chatlens_ai_analyze(
    group_name: str, analysis_type: str = "full"
) -> AIAnalysisResult:
    """对群聊进行智能分析。

    Args:
        group_name: 群聊名称或 talker 标识
        analysis_type: 分析类型，可选值：
            - full: 完整分析（摘要+称号+金句+质量+关键词）
            - summary: 仅生成群聊摘要
            - titles: 仅生成用户称号与人格画像
            - quotes: 仅筛选金句
            - quality: 仅生成质量锐评
            - keywords: 仅提取关键词
    """
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return AIAnalysisResult(
            group_name=group_name,
            analysis_type=analysis_type,
            markdown=f"参数错误: {e}",
        )
    s = _s()
    if not s:
        return AIAnalysisResult(
            group_name=group_name, analysis_type=analysis_type, markdown="服务未初始化"
        )
    messages = s.get_messages(group_name)
    if not messages:
        return AIAnalysisResult(
            group_name=group_name,
            analysis_type=analysis_type,
            markdown=f"未找到 {group_name} 的消息数据。请先使用 chatlens_load_data 加载。",
        )
    ai = s.get_ai()
    if ai.api_key:
        try:
            if analysis_type == "full":
                result = ai.full_analysis(messages)
            elif analysis_type == "summary":
                result = {"summary": ai.generate_summary(messages)}
            elif analysis_type == "titles":
                result = {"user_titles": ai.analyze_user_titles(messages)}
            elif analysis_type == "quotes":
                result = {"golden_quotes": ai.analyze_golden_quotes(messages)}
            elif analysis_type == "quality":
                result = {"chat_quality": ai.analyze_chat_quality(messages)}
            elif analysis_type == "keywords":
                result = {"keywords": ai.extract_keywords(messages)}
            else:
                return AIAnalysisResult(
                    group_name=group_name,
                    analysis_type=analysis_type,
                    markdown=f"未知分析类型: {analysis_type}。可选: full, summary, titles, quotes, quality, keywords",
                )
            markdown_text = json.dumps(result, ensure_ascii=False, indent=2)
            return AIAnalysisResult(
                group_name=group_name,
                analysis_type=analysis_type,
                method="ai",
                data=result,
                markdown=markdown_text,
            )
        except (OSError, ValueError, RuntimeError, ImportError):
            logger.warning("MCP 报告生成失败", exc_info=True)
    from chatlens.core.ai_analyzer import rule_based_analysis

    result = rule_based_analysis(messages)
    if analysis_type == "summary":
        result = {"summary": result.get("summary", {})}
    elif analysis_type == "titles":
        result = {"user_titles": result.get("user_titles", {})}
    elif analysis_type == "quotes":
        result = {"golden_quotes": result.get("golden_quotes", {})}
    elif analysis_type == "quality":
        result = {"chat_quality": result.get("chat_quality", {})}
    elif analysis_type == "keywords":
        result = {"keywords": result.get("keywords", {})}
    note = "（规则分析：未配置 API Key 或 AI 调用失败，已自动降级）"
    markdown_text = json.dumps(result, ensure_ascii=False, indent=2) + f"\n\n⚠️ {note}"
    return AIAnalysisResult(
        group_name=group_name,
        analysis_type=analysis_type,
        method="rule",
        data=result,
        markdown=markdown_text,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_get_user_titles(group_name: str) -> str:
    """获取群聊用户的个性化称号和人格画像。"""
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    messages = s.get_messages(group_name)
    if not messages:
        return f"未找到 {group_name} 的消息数据。"
    result, used_fallback = _ai_or_rule(messages, "analyze_user_titles", "user_titles")
    titles = result.get("user_titles", [])
    if not titles:
        return "未能生成用户称号。"
    lines = [f"🎭 {group_name} 用户称号与人格画像\n"]
    for t in titles:
        lines.append(f"  {t.get('name', '?')} — 「{t.get('title', '?')}」")
        lines.append(
            f"    MBTI: {t.get('mbti', '?')} | 互联网人格: {t.get('sbti', '?')} | 二次元人格: {t.get('acgti', '?')}"
        )
        lines.append(f"    理由: {t.get('reason', '?')}")
        lines.append("")
    if used_fallback:
        lines.append("⚠️ 规则分析：未配置 API Key 或 AI 调用失败，已自动降级")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_get_golden_quotes(group_name: str) -> str:
    """从群聊消息中筛选金句。"""
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    messages = s.get_messages(group_name)
    if not messages:
        return f"未找到 {group_name} 的消息数据。"
    result, used_fallback = _ai_or_rule(
        messages, "analyze_golden_quotes", "golden_quotes"
    )
    quotes = result.get("golden_quotes", [])
    if not quotes:
        return "未筛选到金句。"
    lines = [f"💬 {group_name} 金句集锦\n"]
    for i, q in enumerate(quotes, 1):
        lines.append(f"  {i}. 「{q.get('content', '?')}」")
        lines.append(f"     — {q.get('sender', '?')}")
        lines.append(f"     推荐理由: {q.get('reason', '?')}")
        lines.append("")
    if used_fallback:
        lines.append("⚠️ 规则分析：未配置 API Key 或 AI 调用失败，已自动降级")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_get_chat_quality(group_name: str) -> str:
    """获取群聊质量锐评。"""
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    messages = s.get_messages(group_name)
    if not messages:
        return f"未找到 {group_name} 的消息数据。"
    result, used_fallback = _ai_or_rule(
        messages, "analyze_chat_quality", "chat_quality"
    )
    lines = []
    if result.get("title"):
        lines.append(f"🔥 {result['title']}")
    if result.get("subtitle"):
        lines.append(f"   {result['subtitle']}")
    lines.append("")
    for d in result.get("dimensions", []):
        lines.append(f"  {d.get('name', '?')} ({d.get('percentage', 0)}%)")
        lines.append(f"    {d.get('comment', '')}")
        lines.append("")
    if result.get("summary"):
        lines.append(f"  💡 {result['summary']}")
    if used_fallback:
        lines.append("\n⚠️ 规则分析：未配置 API Key 或 AI 调用失败，已自动降级")
    if not lines:
        return "未能生成质量锐评。"
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_get_recent_messages(group_name: str, count: int = 20) -> str:
    """获取群聊最近的消息记录。"""
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    messages = s.get_messages(group_name)
    if not messages:
        return f"未找到 {group_name} 的消息数据。"
    recent = messages[-count:]
    lines = [f"📝 {group_name} 最近 {len(recent)} 条消息\n"]
    for m in recent:
        fmt = _fmt_msg(m)
        if fmt:
            lines.append("  " + fmt)
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_search_messages(
    group_name: str, keyword: str, count: int = 20, offset: int = 0
) -> str:
    """在群聊消息中搜索包含关键词的消息。

    Args:
        group_name: 群聊名称或 talker 标识
        keyword: 搜索关键词
        count: 返回数量上限，默认 20
        offset: 起始偏移量，默认 0
    """
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    try:
        keyword = _validate_keyword(keyword)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    messages = s.get_messages(group_name)
    if not messages:
        return f"未找到 {group_name} 的消息数据。"
    all_results = []
    for m in messages:
        if m.msg_attr == "system":
            continue
        if m.content and keyword.lower() in m.content.lower():
            all_results.append(m)
    total_count = len(all_results)
    sliced = all_results[offset : offset + count]
    has_more = (offset + count) < total_count
    if not sliced:
        return f"在 {group_name} 中未找到包含「{keyword}」的消息。"
    lines = [
        f"🔍 在 {group_name} 中搜索「{keyword}」，共 {total_count} 条（显示 {offset + 1}-{offset + len(sliced)}）\n"
    ]
    for m in sliced:
        sender = m.sender_remark or m.sender or "未知"
        time_part = ""
        if m.timestamp:
            try:
                time_part = m.timestamp.split(" ")[1][:5]
            except (ValueError, IndexError):
                pass
        lines.append(f"  [{time_part}] {sender}: {m.content[:100]}")
    if has_more:
        lines.append(
            f"\n  ... 还有 {total_count - offset - count} 条，可增大 offset 查看"
        )
    lines.append(f"\ntotal_count: {total_count}, has_more: {has_more}")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, readOnlyHint=False))
def chatlens_refresh_data() -> str:
    """重新解密微信数据库，刷新 chatlog 数据。需要微信正在运行。此操作会覆盖现有解密数据。"""
    s = _s()
    if not s:
        return "服务未初始化"
    try:
        from chatlens.core._chatlog_runtime import run_chatlog_decrypt

        success = run_chatlog_decrypt()
        if success:
            provider = s.get_provider("wechat")
            if provider:
                provider.reset_connections()
            return "✅ 数据库已重新解密并刷新。"
        return "❌ 解密刷新失败，请确认微信正在运行。"
    except (OSError, RuntimeError) as e:
        return f"❌ 刷新失败: {e}"


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, readOnlyHint=False))
def chatlens_delete_data(group_name: str) -> str:
    """删除指定群聊的已加载数据。此操作不可恢复。"""
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    s = _s()
    if not s:
        return "服务未初始化"
    deleted = s.ga.delete_loaded(group_name)
    if deleted:
        return f"✅ 已删除 {group_name} 的数据。"
    return f"❌ 未找到 {group_name} 的数据。"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
def chatlens_schedule_create(
    group_name: str, hour: int, minute: int, theme: str = "scrapbook"
) -> str:
    """创建定时任务，每天定时生成群聊分析报告。

    Args:
        group_name: 群聊名称或 talker 标识
        hour: 执行时间（小时），范围 0-23
        minute: 执行时间（分钟），范围 0-59
        theme: 报告主题，默认 "scrapbook"
    """
    try:
        group_name = _validate_group_name(group_name)
    except ValueError as e:
        return f"参数错误: {e}"
    try:
        _validate_time(hour, minute)
    except ValueError as e:
        return f"参数错误: {e}"
    data = _http_post(
        "/api/schedule/create",
        {"group_name": group_name, "hour": hour, "minute": minute, "theme": theme},
    )
    if data.get("success"):
        return f"✅ 定时任务已创建\n  任务ID: {data['task_id']}\n  群聊: {group_name}\n  执行时间: 每天 {hour:02d}:{minute:02d}\n  主题: {theme}"
    return f"❌ 创建失败: {data.get('error', '未知错误')}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_schedule_list() -> str:
    """列出所有定时任务。"""
    data = _http_get("/api/schedule/list")
    tasks = data.get("tasks", [])
    if not tasks:
        return "当前没有定时任务。使用 chatlens_schedule_create 创建。"
    lines = [f"📋 共 {len(tasks)} 个定时任务：\n"]
    for t in tasks:
        enabled = "✅ 启用" if t.get("enabled", True) else "⏸️ 禁用"
        status_map = {
            "idle": "等待中",
            "running": "执行中",
            "completed": "已完成",
            "failed": "失败",
            "timeout": "超时",
        }
        status = status_map.get(t.get("status", ""), t.get("status", ""))
        lines.append(f"  任务ID: {t['task_id']}")
        lines.append(f"  群聊: {t['group_name']}")
        lines.append(f"  时间: 每天 {t['hour']:02d}:{t['minute']:02d}")
        lines.append(f"  状态: {enabled} | {status}")
        if t.get("last_run"):
            lines.append(f"  上次执行: {t['last_run']}")
        history = t.get("history", [])
        if history:
            lines.append("  最近执行记录:")
            for h in history[:3]:
                icon = "✅" if h.get("success") else "❌"
                lines.append(
                    f"    {icon} {h['time']} ({h.get('method', '')}){(' - ' + h['error']) if h.get('error') else ''}"
                )
        lines.append("")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
def chatlens_schedule_trigger(task_id: str) -> str:
    """手动触发执行一个定时任务。"""
    data = _http_post("/api/schedule/trigger", {"task_id": task_id})
    if data.get("success"):
        return f"✅ 已触发任务 {task_id}，正在后台执行。完成后可在报告历史中查看。"
    return f"❌ 触发失败: {data.get('error', '未知错误')}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
def chatlens_schedule_delete(task_id: str) -> str:
    """删除一个定时任务。"""
    data = _http_delete("/api/schedule/delete", {"task_id": task_id})
    if data.get("success"):
        return f"✅ 已删除定时任务 {task_id}"
    return f"❌ 删除失败: {data.get('error', '未知错误')}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
def chatlens_schedule_toggle(task_id: str, enabled: bool = True) -> str:
    """启用或禁用一个定时任务。

    Args:
        task_id: 任务 ID
        enabled: True 启用，False 禁用，默认 True
    """
    data = _http_post("/api/schedule/toggle", {"task_id": task_id, "enabled": enabled})
    if data.get("success"):
        status = "启用" if enabled else "禁用"
        return f"✅ 已{status}定时任务 {task_id}"
    return f"❌ 操作失败: {data.get('error', '未知错误')}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_check_pending() -> str:
    """检查 Web 端提交的待分析任务。"""
    data = _http_get("/api/ide/tasks/pending")
    tasks = data.get("tasks", [])
    if not tasks:
        return "✅ 当前没有待处理的 IDE 分析任务。"
    lines = [f"📋 发现 {len(tasks)} 个待处理任务：\n"]
    for t in tasks:
        lines.append(f"  任务ID: {t['task_id']}")
        lines.append(f"  群聊: {t['group_name']}")
        lines.append(f"  消息数: {t['message_count']}")
        lines.append(f"  主题: {t['theme']}, 格式: {t['fmt']}")
        lines.append(f"  创建时间: {t['created_at']}")
        lines.append("")
    lines.append(
        "请使用 chatlens_get_messages_for_ai 获取消息，分析后调用 chatlens_submit_analysis 提交结果。"
    )
    lines.append(
        "💡 提示：你也可以调用 chatlens_wait_for_task 阻塞等待新任务（更省 token）。"
    )
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def chatlens_wait_for_task(timeout_seconds: int = 300) -> str:
    """阻塞等待新的 IDE 分析任务（进程内推送，无需轮询）。

    这是 chatlens_check_pending 的优化版：当没有任务时，IDE 端调用本工具
    会一直阻塞，直到 Web 端提交新任务或超时。比反复调用 check_pending 更高效。

    Args:
        timeout_seconds: 最长等待秒数，默认 300（5 分钟）。超时返回空结果。
    """
    s = _s()
    if not s:
        return "服务未初始化"
    ide_tasks = getattr(getattr(s.ga, "web", None), "ide_tasks", None)
    if ide_tasks is None:
        return "⚠️ IDE 任务队列不可用，请回退到 chatlens_check_pending 轮询模式。"

    # 1) 先查一次 backlog + pending，避免错过已存在的任务
    pending = ide_tasks.get_pending().get("tasks", [])
    if pending:
        t = pending[0]
        return (
            f"📋 发现待处理任务：\n"
            f"  任务ID: {t['task_id']}\n"
            f"  群聊: {t['group_name']}\n"
            f"  消息数: {t['message_count']}\n"
            f"  主题: {t['theme']}, 格式: {t['fmt']}\n\n"
            f"请使用 chatlens_get_messages_for_ai 获取消息，分析后调用 chatlens_submit_analysis 提交结果。"
        )

    # 2) 注册监听器，阻塞等待
    q = ide_tasks.subscribe(maxsize=10)
    try:
        try:
            event = q.get(timeout=timeout_seconds)
        except Exception:
            return f"⏰ 等待 {timeout_seconds}s 内无新任务。"

        if event.get("type") == "task_created":
            return (
                f"📋 新任务到达：\n"
                f"  任务ID: {event.get('task_id')}\n"
                f"  群聊: {event.get('group_name')}\n"
                f"  消息数: {event.get('message_count')}\n"
                f"  主题: {event.get('theme')}, 格式: {event.get('fmt')}\n\n"
                f"请使用 chatlens_get_messages_for_ai 获取消息，分析后调用 chatlens_submit_analysis 提交结果。"
            )
        # 收到其他类型事件 → 说明队列还有活动但没新任务，继续等
        return f"ℹ️ 收到事件: {event.get('type')}，但没有新任务。请重试 chatlens_wait_for_task。"
    finally:
        ide_tasks.unsubscribe(q)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
def chatlens_submit_analysis(
    task_id: str,
    ai_data: str = "{}",
    ai_summary: str = "",
    ai_topics: str = "[]",
    ai_user_titles: str = "[]",
    ai_golden_quotes: str = "[]",
    ai_chat_quality: str = "",
    ai_keywords: str = "[]",
) -> str:
    """提交 IDE AI 分析结果到 Web 端。

    Args:
        task_id: 任务 ID
        ai_data: AI 分析数据 JSON 字符串（优先，格式同 chatlens_generate_report_image 的 ai_data）
        ai_summary ~ ai_keywords: 向后兼容的独立参数（ai_data 优先）
    """
    parsed = _parse_ai_data(
        ai_data=ai_data,
        ai_summary=ai_summary,
        ai_topics=ai_topics,
        ai_user_titles=ai_user_titles,
        ai_golden_quotes=ai_golden_quotes,
        ai_chat_quality=ai_chat_quality,
        ai_keywords=ai_keywords,
    )
    # 构造提交格式
    result = {
        "summary": parsed.get(
            "summary",
            {"summary": "", "topics": [], "key_points": [], "action_items": []},
        ),
        "user_titles": parsed.get("user_titles", {"user_titles": []}),
        "golden_quotes": parsed.get("golden_quotes", {"golden_quotes": []}),
        "chat_quality": parsed.get("chat_quality", {}),
        "keywords": parsed.get("keywords", {"keywords": []}),
    }
    # 向后兼容：如果 ai_data 为空且使用了旧参数，补充结构
    if not parsed.get("summary") and ai_summary:
        result["summary"] = {
            "summary": ai_summary,
            "topics": json.loads(ai_topics) if ai_topics else [],
            "key_points": [],
            "action_items": [],
        }
    if not parsed.get("user_titles") and ai_user_titles:
        result["user_titles"] = {
            "user_titles": json.loads(ai_user_titles) if ai_user_titles else []
        }
    if not parsed.get("golden_quotes") and ai_golden_quotes:
        result["golden_quotes"] = {
            "golden_quotes": json.loads(ai_golden_quotes) if ai_golden_quotes else []
        }
    if not parsed.get("chat_quality") and ai_chat_quality:
        result["chat_quality"] = json.loads(ai_chat_quality) if ai_chat_quality else {}
    if not parsed.get("keywords") and ai_keywords:
        result["keywords"] = {
            "keywords": json.loads(ai_keywords) if ai_keywords else []
        }

    data = _http_post("/api/ide/task/result", {"task_id": task_id, "result": result})
    if data.get("success"):
        return f"✅ 分析结果已提交到 Web 端，任务 {task_id}。"
    return f"❌ 提交失败: {data.get('error', '未知错误')}"


@mcp.resource("chatlog://status")
def chatlog_status() -> str:
    """chatlog 数据源连接状态"""
    s = _s()
    if not s:
        return "服务未初始化"
    provider = s.get_provider("wechat")
    if not provider:
        return "wechat provider 未配置"
    available = provider.is_available()
    bridge = provider.bridge
    return f"chatlog 可用: {'是' if available else '否'}\n" + (
        f"API 地址: {bridge.api_base}\n数据库: {bridge.db_path or '未找到'}"
        if available
        else ""
    )


def setup(ga):
    svc = MCPService(ga)
    set_service(svc)
    ga.mcp = svc
    logger.info("MCP 插件已注册")


def run_server():
    # 独立运行时初始化 _service
    if get_service() is None:
        from chatlens.main import _build_ga

        ga = _build_ga()
        setup(ga)
    mcp.run()


if __name__ == "__main__":
    run_server()

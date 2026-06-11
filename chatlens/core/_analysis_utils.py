"""分析工具函数 — 从 _analysis_data.py 提取的可复用逻辑"""

import json
import re
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple

from .models import ChatMessage
from ._analysis_data import PERSONALITY_TEMPLATES, AI_USER_STATS_DEFAULT, _MSG_FMT


def build_fallback_vibe(chat_pct, image_pct, reply_pct, night_pct, other_pct):
    """返回 4-5 个质量维度。other_pct 较低时省略"其他"维度，避免噪音。"""
    dims = [
        {
            "name": "💬 日常闲聊",
            "percentage": min(chat_pct, 60),
            "comment": f"文字消息占比{chat_pct}%，群友热情交流中" if chat_pct > 0 else "暂无文字消息",
            "color": "#e07850",
        },
        {
            "name": "🖼️ 图片分享",
            "percentage": min(image_pct, 30),
            "comment": f"图片占比{image_pct}%，视觉丰富度{'较高' if image_pct > 15 else '适中'}" if image_pct > 0 else "暂无图片分享",
            "color": "#d4a853",
        },
        {
            "name": "🔄 互动回复",
            "percentage": min(reply_pct, 25),
            "comment": f"引用回复占比{reply_pct}%，互动{'频繁' if reply_pct > 10 else '适中'}" if reply_pct > 0 else "暂无互动回复",
            "color": "#4a9b8c",
        },
        {
            "name": "🌙 深夜活跃",
            "percentage": min(night_pct, 20),
            "comment": f"凌晨消息占比{night_pct}%，{'夜猫子聚集地' if night_pct > 10 else '作息规律'}" if night_pct > 0 else "作息规律，没有深夜发言",
            "color": "#8b6bb3",
        },
    ]
    # other_pct >= 5 时才追加"其他内容"维度：占比过低时只算噪音
    if other_pct >= 5:
        dims.append({
            "name": "📎 其他内容",
            "percentage": other_pct,
            "comment": f"语音、表情等多元内容，占比{other_pct}%" if other_pct > 0 else "暂无语音/表情等其他内容",
            "color": "#5b8cd0",
        })
    return dims


def format_messages_id_only(
    messages: List[ChatMessage], max_messages: int = 200
) -> Tuple[str, Dict[str, str]]:
    id_map: Dict[str, str] = {}
    counter = 1
    formatted = []
    for m in messages[:max_messages]:
        if m.msg_attr in ("system", "time"):
            continue
        sender = m.sender_remark or m.sender or "未知"
        if sender not in id_map:
            id_map[sender] = f"U{counter}"
            counter += 1
        uid = id_map[sender]
        try:
            time_part = m.timestamp.split(" ")[1][:5] if m.timestamp else "??:??"
        except (ValueError, IndexError):
            time_part = "??:??"
        if m.msg_type == "text":
            formatted.append(f"[{time_part}] [{uid}]: {m.content}")
        elif m.msg_type == "quote":
            formatted.append(
                f"[{time_part}] [{uid}]: (引用) {m.quote_content} | {m.content}"
            )
        elif m.msg_type in _MSG_FMT:
            formatted.append(f"[{time_part}] [{uid}]: {_MSG_FMT[m.msg_type]}")
        else:
            formatted.append(f"[{time_part}] [{uid}]: [{m.msg_type}] {m.content}")
    return "\n".join(formatted), id_map


def build_id_map_text(id_map: Dict[str, str]) -> str:
    return "\n".join(
        f"{uid} = {name}"
        for name, uid in sorted(id_map.items(), key=lambda x: int(x[1][1:]))
    )


def replace_ids_in_string(text: str, id_map: Dict[str, str]) -> str:
    for name, uid in id_map.items():
        text = text.replace(uid, name)
    return text


def map_ids_back(data: Any, id_map: Dict[str, str]) -> Any:
    if isinstance(data, dict):
        return {
            k: map_ids_back(v, id_map)
            if isinstance(v, (dict, list))
            else replace_ids_in_string(v, id_map)
            if isinstance(v, str)
            else v
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [map_ids_back(item, id_map) for item in data]
    if isinstance(data, str):
        return replace_ids_in_string(data, id_map)
    return data


def inject_personality(system_prompt: str, prompt_type: str) -> str:
    tpl = PERSONALITY_TEMPLATES.get(prompt_type, PERSONALITY_TEMPLATES["summary"])
    woven = []
    for line in system_prompt.split("\n"):
        woven.append(line)
        if "JSON" in line or "json" in line:
            woven.append(f"\n{tpl['weave']}\n")
    return "\n".join([tpl["head"], "", "\n".join(woven), "", tpl["tail"]])


def try_regex_extract(text: str, schema: Dict) -> Optional[dict]:
    result = {}
    for key, value in schema.items():
        if isinstance(value, list):
            match = re.search(rf'"{key}"\s*:\s*\[([\s\S]*?)\]', text)
            if match:
                try:
                    result[key] = json.loads("[" + match.group(1) + "]")
                except json.JSONDecodeError:
                    result[key] = value
            else:
                result[key] = value
        elif isinstance(value, str):
            match = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
            result[key] = match.group(1) if match else value
        elif isinstance(value, (int, float)):
            match = re.search(rf'"{key}"\s*:\s*(\d+\.?\d*)', text)
            result[key] = type(value)(match.group(1)) if match else value
    return result if result else None


def parse_json_response(result: str, schema: Optional[Dict] = None) -> Optional[dict]:
    if not result:
        return None
    cleaned = result.strip()
    for prefix in ("```json", "```"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        match = re.search(pattern, cleaned)
        if match:
            try:
                parsed = json.loads(match.group())
                return {"items": parsed} if isinstance(parsed, list) else parsed  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass
    return try_regex_extract(cleaned, schema) if schema else None


def get_user_stats(messages: List[ChatMessage]) -> Dict[str, Dict]:
    stats: Dict[str, Dict] = {}
    for m in messages:
        if m.msg_attr == "system":
            continue
        sender = m.sender_remark or m.sender or "未知"
        if sender not in stats:
            stats[sender] = dict(AI_USER_STATS_DEFAULT)
            stats[sender]["hours"] = Counter()
        s = stats[sender]
        s["message_count"] += 1
        s["char_count"] += len(m.content or "")
        if m.msg_type == "image":
            s["image_count"] += 1
        elif m.msg_type == "voice":
            s["voice_count"] += 1
        elif m.msg_type == "quote":
            s["reply_count"] += 1
        elif m.msg_type == "emotion":
            s["emoji_count"] += 1
        try:
            hour = int(m.timestamp.split(" ")[1].split(":")[0]) if m.timestamp else -1
            if 0 <= hour < 6:
                s["night_count"] += 1
            if hour >= 0:
                s["hours"][hour] += 1
        except (ValueError, IndexError):
            pass
    return stats

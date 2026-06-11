import re
import logging
from datetime import datetime
from typing import List, Dict, Any
from collections import Counter

from .models import ChatMessage

logger = logging.getLogger("chatlens.analyzer")


class GroupStatsAnalyzer:
    def __init__(self) -> None:
        from ._analysis_data import STOP_WORDS

        self._stop_words = STOP_WORDS

    def analyze(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        if not messages:
            return self._empty_result()
        return {
            "overview": self._overview(messages),
            "member_stats": self._member_stats(messages),
            "msg_type_distribution": self._msg_type_distribution(messages),
            "hourly_distribution": self._hourly_distribution(messages),
            "daily_trend": self._daily_trend(messages),
            "weekday_distribution": self._weekday_distribution(messages),
            "keyword_cloud": self._keyword_analysis(messages),
            "interaction_analysis": self._interaction_analysis(messages),
        }

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "overview": {
                "total_messages": 0,
                "total_members": 0,
                "time_range": {"start": "", "end": ""},
                "avg_messages_per_day": 0,
            },
            "member_stats": [],
            "msg_type_distribution": [],
            "hourly_distribution": [],
            "daily_trend": [],
            "weekday_distribution": [],
            "keyword_cloud": [],
            "interaction_analysis": {"top_interactions": [], "reply_rate": 0},
        }

    def _overview(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        senders = set()
        timestamps = []
        for m in messages:
            if m.sender:
                senders.add(m.sender)
            if m.timestamp:
                timestamps.append(m.timestamp)
        time_range = {"start": "", "end": ""}
        avg_per_day: float = 0
        if timestamps:
            sorted_ts = sorted(timestamps)
            time_range["start"] = sorted_ts[0]
            time_range["end"] = sorted_ts[-1]
            try:
                start_dt = datetime.strptime(sorted_ts[0][:10], "%Y-%m-%d")
                end_dt = datetime.strptime(sorted_ts[-1][:10], "%Y-%m-%d")
                days = max((end_dt - start_dt).days, 1)
                avg_per_day = round(len(messages) / days, 1)
            except (ValueError, IndexError):
                pass
        return {
            "total_messages": len(messages),
            "total_members": len(senders),
            "time_range": time_range,
            "avg_messages_per_day": avg_per_day,
        }

    def _member_stats(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        from ._analysis_utils import get_user_stats

        raw_stats = get_user_stats(messages)
        total = sum(s["message_count"] for s in raw_stats.values()) or 1
        result = []
        for sender, s in sorted(
            raw_stats.items(), key=lambda x: x[1]["message_count"], reverse=True
        ):
            text_count = (
                s["message_count"]
                - s["image_count"]
                - s["voice_count"]
                - s["emoji_count"]
            )
            other_count = s["voice_count"] + s["emoji_count"]
            result.append(
                {
                    "sender": sender,
                    "msg_count": s["message_count"],
                    "text_count": text_count,
                    "image_count": s["image_count"],
                    "other_count": other_count,
                    "total_chars": s["char_count"],
                    "msg_percentage": round(s["message_count"] / total * 100, 1),
                    "avg_chars_per_msg": round(s["char_count"] / max(text_count, 1), 1),
                }
            )
        return result

    def _msg_type_distribution(
        self, messages: List[ChatMessage]
    ) -> List[Dict[str, Any]]:
        type_counter: Counter = Counter()
        for m in messages:
            if m.msg_attr in ("system", "time"):
                continue
            type_counter[m.msg_type] += 1
        total = sum(type_counter.values()) or 1
        type_labels = {
            "text": "文本",
            "image": "图片",
            "voice": "语音",
            "video": "视频",
            "file": "文件",
            "link": "链接",
            "emotion": "表情",
            "quote": "引用",
            "merge": "合并转发",
            "location": "位置",
            "personal_card": "名片",
            "note": "笔记",
            "other": "其他",
        }
        return [
            {
                "type": t,
                "label": type_labels.get(t, t),
                "count": c,
                "percentage": round(c / total * 100, 1),
            }
            for t, c in type_counter.most_common()
        ]

    def _hourly_distribution(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        hour_counter: Counter = Counter()
        for m in messages:
            try:
                ts = m.timestamp
                if ts and len(ts) >= 16:
                    hour = int(ts[11:13])
                    hour_counter[hour] += 1
            except (ValueError, IndexError):
                pass
        return [
            {"hour": h, "count": hour_counter.get(h, 0), "label": f"{h:02d}:00"}
            for h in range(24)
        ]

    def _daily_trend(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        day_counter: Counter = Counter()
        for m in messages:
            try:
                ts = m.timestamp
                if ts and len(ts) >= 10:
                    day_counter[ts[:10]] += 1
            except (ValueError, IndexError):
                pass
        return [{"date": d, "count": c} for d, c in sorted(day_counter.items())]

    def _weekday_distribution(
        self, messages: List[ChatMessage]
    ) -> List[Dict[str, Any]]:
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday_counter: Counter = Counter()
        for m in messages:
            try:
                ts = m.timestamp
                if ts and len(ts) >= 10:
                    dt = datetime.strptime(ts[:10], "%Y-%m-%d")
                    weekday_counter[dt.weekday()] += 1
            except (ValueError, IndexError):
                pass
        return [
            {
                "weekday": i,
                "label": weekday_names[i],
                "count": weekday_counter.get(i, 0),
            }
            for i in range(7)
        ]

    def _keyword_analysis(
        self, messages: List[ChatMessage], top_n: int = 50
    ) -> List[Dict[str, Any]]:
        word_counter: Counter = Counter()
        pattern = re.compile(r"[\u4e00-\u9fff]+")
        for m in messages:
            if m.msg_type != "text" or not m.content:
                continue
            for segment in pattern.findall(m.content):
                if len(segment) >= 2:
                    for i in range(len(segment) - 1):
                        bigram = segment[i : i + 2]
                        if (
                            bigram[0] not in self._stop_words
                            and bigram[1] not in self._stop_words
                        ):
                            word_counter[bigram] += 1
        return [{"word": w, "count": c} for w, c in word_counter.most_common(top_n)]

    def _interaction_analysis(
        self, messages: List[ChatMessage], min_content_len: int = 4
    ) -> Dict[str, Any]:
        interaction_counter: Counter = Counter()
        quote_count = 0
        human_msg_count = 0
        # 预建内容→发送者索引，避免 O(n²) 嵌套循环
        content_to_sender: Dict[str, str] = {}
        for m in messages:
            if m.msg_attr in ("system", "time"):
                continue
            human_msg_count += 1
            if m.content and m.sender and len(m.content) >= min_content_len:
                content_to_sender.setdefault(m.content, m.sender)
        for m in messages:
            if m.msg_attr in ("system", "time"):
                continue
            if m.msg_type == "quote" and m.quote_content:
                quote_count += 1
                for content, sender in content_to_sender.items():
                    if sender != m.sender and content in m.quote_content:
                        pair = tuple(sorted([m.sender, sender]))
                        interaction_counter[pair] += 1
                        break
        top_interactions = [
            {"pair": list(pair), "count": count}
            for pair, count in interaction_counter.most_common(10)
        ]
        reply_rate = round(quote_count / max(human_msg_count, 1) * 100, 1)
        return {"top_interactions": top_interactions, "reply_rate": reply_rate}

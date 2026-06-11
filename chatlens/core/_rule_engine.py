from typing import List, Dict, Any
import copy
import re
from collections import Counter

from .models import ChatMessage
from ._analysis_data import (
    SBTI_MAP,
    ACGTI_MAP,
    TITLE_POOL,
    STOP_WORDS,
    VIBE_DATA,
    REASON_MAP,
    DEFAULT_REASONS,
    QUOTE_SCORING_RULES,
    KEYPOINT_OPINION_KW,
    KEYPOINT_DEPTH_KW,
    PEAK_HOUR_DESC,
    RULE_USER_STATS_DEFAULT,
    EMPTY_RESULT,
)
from ._analysis_utils import build_fallback_vibe  # 保留导入，AI 分析仍使用


def _collect_user_stats(messages: List[ChatMessage]):
    """遍历消息，收集用户统计、文本消息、时间分布等基础数据"""
    user_stats: Dict[str, Dict] = {}
    text_msgs: List[ChatMessage] = []
    hourly_counts: Dict[int, int] = {}
    night_count = reply_count = image_count = 0
    date_counts: Dict[str, int] = {}
    sender_msgs: Dict[str, List[str]] = {}

    for m in messages:
        if m.msg_attr == "system":
            continue
        sender = m.sender_remark or m.sender or "未知"
        if sender not in user_stats:
            user_stats[sender] = dict(RULE_USER_STATS_DEFAULT)
        s = user_stats[sender]
        s["message_count"] += 1
        s["char_count"] += len(m.content or "")
        if m.msg_type == "image":
            s["image_count"] += 1
            image_count += 1
        elif m.msg_type == "voice":
            s["voice_count"] += 1
        elif m.msg_type == "quote":
            s["reply_count"] += 1
            reply_count += 1
        elif m.msg_type == "emotion":
            s["emoji_count"] += 1
        if m.msg_type == "text" and m.content and len(m.content) >= 5:
            text_msgs.append(m)
            sender_msgs.setdefault(sender, []).append(m.content)
        if m.msg_type == "text" and m.content and len(m.content) >= 50:
            s["long_msg_count"] += 1
        try:
            hour = int(m.timestamp.split(" ")[1].split(":")[0]) if m.timestamp else -1
            hourly_counts[hour] = hourly_counts.get(hour, 0) + 1
            if 0 <= hour < 6:
                s["night_count"] += 1
                night_count += 1
            day = (m.timestamp or "")[:10]
            if day:
                date_counts[day] = date_counts.get(day, 0) + 1
        except (ValueError, IndexError):
            pass

    return (
        user_stats,
        text_msgs,
        hourly_counts,
        night_count,
        reply_count,
        image_count,
        date_counts,
        sender_msgs,
    )


def _extract_keywords(text_msgs: List[ChatMessage]) -> tuple:
    """从文本消息中提取关键词，同时返回每条消息的分词结果供后续复用"""
    word_counter: Counter = Counter()
    msg_words: Dict[int, List[str]] = {}
    emoji_pattern = re.compile(r"\[[^\[\]]{1,10}\]")
    try:
        import jieba

        for idx, m in enumerate(text_msgs):
            words = [
                w
                for w in jieba.lcut(emoji_pattern.sub("", m.content or ""))
                if len(w) >= 2 and w not in STOP_WORDS and not w.isdigit()
            ]
            msg_words[id(m)] = words
            for w in words:
                word_counter[w] += 1
    except ImportError:
        for idx, m in enumerate(text_msgs):
            words = [
                w
                for w in re.findall(
                    r"[\u4e00-\u9fff]{2,4}", emoji_pattern.sub("", m.content or "")
                )
                if w not in STOP_WORDS and not w.isdigit()
            ]
            msg_words[id(m)] = words
            for w in words:
                word_counter[w] += 1

    top_keywords = [w for w, _ in word_counter.most_common(10)]
    keywords_list = [
        {"word": w, "relevance": min(10, c // 2 + 1)}
        for w, c in word_counter.most_common(20)
    ]
    return word_counter, top_keywords, keywords_list, msg_words


def _assign_user_titles(active_users, total: int) -> list:
    """为活跃用户分配称号和 MBTI"""
    user_titles_list = []
    for i, (name, s) in enumerate(active_users[:15]):
        mc = s["message_count"]
        avg_chars = round(s["char_count"] / mc, 1) if mc else 0
        nr = s["night_count"] / mc if mc else 0
        rr = s["reply_count"] / mc if mc else 0
        ir = s["image_count"] / mc if mc else 0
        er = s["emoji_count"] / mc if mc else 0
        lr = s["long_msg_count"] / mc if mc else 0

        if i == 0:
            title, td = "社群话事人", "群内C位，发言量断层第一"
        elif nr > 0.3:
            title, td = "夜猫子", f"凌晨{round(nr * 100)}%的消息都来自ta"
        elif ir > 0.3:
            title, td = "图王", f"图片占比{round(ir * 100)}%，视觉动物"
        elif er > 0.3:
            title, td = "表情帝", "表情包比文字多，一个emoji胜千言"
        elif lr > 0.2:
            title, td = "深度思考者", "每条长文都是一篇小论文"
        elif avg_chars > 30:
            title, td = "长文选手", f"平均{avg_chars}字/条，字字珠玑"
        elif rr > 0.3:
            title, td = "互动达人", "有问必答，群内社交天花板"
        elif mc <= 3:
            title, td = "潜水员", "深水静流，偶尔冒泡证明存在"
        else:
            title, td = TITLE_POOL[(i + 3) % len(TITLE_POOL)]

        mbti_guess, mbti_reason = _guess_mbti(avg_chars, er, ir, lr, mc, nr, rr, total)

        rt = _build_title_reason(i, mc, nr, avg_chars, ir, rr, mbti_reason)

        user_titles_list.append(
            {
                "name": name,
                "title": title,
                "mbti": mbti_guess,
                "sbti": SBTI_MAP.get(mbti_guess, "未知生物"),
                "acgti": ACGTI_MAP.get(mbti_guess, "未知角色"),
                "reason": rt,
            }
        )
    return user_titles_list


def _guess_mbti(avg_chars, er, ir, lr, mc, nr, rr, total) -> tuple:
    """根据用户行为特征推断 MBTI — 基于 E/I、S/N、T/F、J/P 四维度交叉判断"""

    # ── E/I 维度：外向 vs 内向 ──
    # 消息占比高 → E；消息少 → I
    activity_ratio = mc / total if total else 0
    if activity_ratio > 0.06 or mc > 50:
        ei = "E"
        ei_desc = "活跃外向"
    elif mc > 20 and rr > 0.1:
        ei = "E"
        ei_desc = "乐于表达"
    elif mc > 8:
        ei = "I"
        ei_desc = "沉稳内敛"
    else:
        ei = "I"
        ei_desc = "深藏不露"

    # ── S/N 维度：实感 vs 直觉 ──
    # 图片多、表情多 → S（关注具体事物）；长文比例高 → N（关注抽象概念）
    # 注意：avg_chars 容易被少数长消息拉高，所以用 lr（长消息比例）更可靠
    if lr > 0.2:
        sn = "N"
        sn_desc = "深度思考"
    elif ir > 0.15 or er > 0.15:
        sn = "S"
        sn_desc = "关注当下"
    elif lr > 0.05 or avg_chars > 40:
        sn = "N"
        sn_desc = "善于思辨"
    elif mc > 10 and rr > 0.05:
        # 有一定互动量的用户，偏 S（更关注具体社交）
        sn = "S"
        sn_desc = "务实接地气"
    else:
        # 低活跃用户：根据消息数区分，多发消息偏 N，少发偏 S
        sn = "N" if mc > 8 else "S"
        sn_desc = "善于思辨" if mc > 8 else "务实接地气"

    # ── T/F 维度：思维 vs 情感 ──
    # 表情多、互动多 → F；长文比例高、逻辑性强 → T
    if er > 0.2 or rr > 0.25:
        tf = "F"
        tf_desc = "情感丰富"
    elif lr > 0.15:
        tf = "T"
        tf_desc = "理性分析"
    elif er > 0.05 or (rr > 0.1 and mc > 10):
        tf = "F"
        tf_desc = "表达细腻"
    elif avg_chars > 50 and lr > 0.05:
        tf = "T"
        tf_desc = "逻辑缜密"
    elif rr > 0.03 and mc > 5:
        # 有一定回复习惯的偏 F
        tf = "F"
        tf_desc = "热情洋溢"
    else:
        # 默认偏 T（沉默思考型），除非消息很多
        tf = "T" if mc <= 20 else "F"
        tf_desc = "冷静克制" if mc <= 20 else "热情洋溢"

    # ── J/P 维度：判断 vs 感知 ──
    # 深夜活跃 → P；规律发言 → J；图片/表情多 → P
    if nr > 0.15:
        jp = "P"
        jp_desc = "随性自由"
    elif ir > 0.1 or er > 0.1:
        jp = "P"
        jp_desc = "随兴所至"
    elif mc > 30 and rr > 0.05:
        jp = "J"
        jp_desc = "有条不紊"
    elif nr > 0.05 or lr > 0.05:
        jp = "P"
        jp_desc = "灵光乍现"
    elif mc > 20:
        jp = "J"
        jp_desc = "持之以恒"
    else:
        # 低活跃用户默认 P（随缘出没）
        jp = "P" if mc <= 10 else "J"
        jp_desc = "随缘出没" if mc <= 10 else "沉稳有序"

    mbti = ei + sn + tf + jp

    # MBTI 简要理由映射
    mbti_reasons = {
        "ESTJ": "高效组织者，群内秩序维护者",
        "ESTP": "行动派，永远冲在最前面",
        "ESFJ": "温暖社交家，群内气氛担当",
        "ESFP": "快乐源泉，活在当下的派对动物",
        "ENTJ": "天生领袖，群内决策核心",
        "ENTP": "辩论王者，永远有话说的思想者",
        "ENFJ": "社交教父，天生的群聊灵魂人物",
        "ENFP": "创意无限，群内的快乐小狗",
        "ISTJ": "规则守护者，默默维持群内秩序",
        "ISTP": "冷面匠人，深夜出没的独行侠",
        "ISFJ": "温柔后盾，默默关心每个人的暖阳",
        "ISFP": "随性艺术家，用图片和表情表达自我",
        "INTJ": "策略大师，默默观察的幕后智囊",
        "INTP": "逻辑怪才，长文输出缜密思考",
        "INFJ": "心灵导师，洞察人心的引路人",
        "INFP": "理想主义诗人，用文字传递温度",
    }

    reason = mbti_reasons.get(mbti, f"{ei_desc}·{sn_desc}·{tf_desc}·{jp_desc}")
    return mbti, reason


def _build_title_reason(i, mc, nr, avg_chars, ir, rr, mbti_reason) -> str:
    """构建用户称号理由文本"""
    if i == 0:
        rt = f"以{mc}条消息断层领先，群内绝对C位"
    elif nr > 0.2:
        rt = f"凌晨时段贡献了{round(nr * 100)}%的发言"
    elif avg_chars > 20:
        rt = f"平均每条{avg_chars}字，内容密度极高"
    elif ir > 0.2:
        rt = f"图片占比{round(ir * 100)}%，视觉型选手"
    elif rr > 0.2:
        rt = f"回复占比{round(rr * 100)}%，社交互动达人"
    elif mc >= 10:
        rt = f"贡献了{mc}条消息，群内中坚力量"
    elif mc <= 3:
        rt = f"仅{mc}条发言，深藏功与名"
    else:
        rt = f"发言{mc}条，低调但不可或缺"
    if mbti_reason:
        rt += f"。{mbti_reason}"
    return rt


# 招聘 JD 过滤关键词
JD_FILTER_KW = [
    "岗位职责", "任职要求", "投递邮箱", "简历投递", "岗位描述",
    "工作职责", "任职资格", "薪资范围", "福利待遇", "招聘",
    "实习生", "校招", "社招", "内推", "HC", "headcount",
    "本科及以上", "硕士及以上", "工作经验", "五险一金",
    # 招聘/推广动词
    "来投", "来聊", "欢迎投递", "欢迎来", "扫码加入",
    "投递方式", "简历发送",
    # 招聘缩写/常见词
    "急招", "jd", "JD", "继任", "base", "Base",
    "招人", "找人", "急聘", "高薪",
]

# 活动/推广/广告过滤关键词
PROMO_FILTER_KW = [
    "活动来啦", "新活动", "活动通知", "活动预告", "活动啦",
    "免费领", "红包", "优惠券", "口令", "复制口令",
    "小程序卡片", "小程序上线", "Bot上线", "bot上线",
    "参与内测", "内测", "收集问题", "统一解答",
    "福利", "抽奖", "转发", "集赞",
    "淘宝", "京东", "拼多多", "美团",
    "闪购", "无门槛", "先到先得",
    # 推广/运营类
    "蹲投稿", "投稿", "期待你的分享", "让经验流动",
    "邀请了一位", "博主", "5k粉", "大厂offer",
    "暑期实习", "秋招", "春招", "求职", "面试干货",
    "上岸心得", "信息差", "经验分享", "来听",
    "欢迎来", "欢迎大家", "扫码", "私信",
    # 广告/电商
    "下单", "购买", "满减",
]

# 金句长度上限
GOLDEN_QUOTE_MAX_LEN = 150


def _score_golden_quotes(text_msgs, user_stats) -> list:
    """评分并筛选金句 — 过滤招聘 JD、活动推广、广告和超长消息"""
    scored_msgs = []
    for m in text_msgs:
        content = m.content or ""

        # 长度下限过滤：少于 25 字的消息（短回复、表情）不适合做金句
        if len(content) < 25:
            continue
        # 长度上限过滤：超过 150 字的消息直接跳过
        if len(content) > GOLDEN_QUOTE_MAX_LEN:
            continue

        # 招聘 JD 内容过滤：包含招聘关键词的直接跳过
        if any(kw in content for kw in JD_FILTER_KW):
            continue

        # 活动/推广/广告过滤：包含推广关键词的直接跳过
        if any(kw in content for kw in PROMO_FILTER_KW):
            continue

        score = 0
        quote_type = ""
        # 适中的长度加分（30-80 字最佳，参考金句都在这个范围）
        if 30 <= len(content) <= 80:
            score += 3
        elif len(content) >= 25:
            score += 1

        for qtype, kws, pts, override in QUOTE_SCORING_RULES:
            if any(kw in content for kw in kws):
                score += pts
                if override or not quote_type:
                    quote_type = qtype

        # ── 观点/思考特征加分（金句的核心特征） ──
        # 第一人称观点
        thought_kw = [
            "我觉得", "我认为", "我感觉", "在我看来", "我的看法",
            "其实", "说白了", "本质", "核心", "关键", "根本",
            "其实不然", "说实话", "坦白说", "真的",
        ]
        thought_hits = sum(1 for kw in thought_kw if kw in content)
        if thought_hits >= 2:
            score += 4
        elif thought_hits >= 1:
            score += 2

        # 经验/方法/学习类（"用...学"、"我...了"、"怎么..."）
        experience_kw = ["用过", "试过", "学到了", "学会了", "学会了", "怎么用",
                         "怎么学", "如何", "方法", "建议", "推荐", "技巧", "经验",
                         "我的", "我们"]
        experience_hits = sum(1 for kw in experience_kw if kw in content)
        if experience_hits >= 2:
            score += 2

        # 反差/转折/自嘲（"其实...但是"、"觉得...但"、"我...呵呵"）
        contrast_kw = ["但是", "不过", "然而", "反而", "倒是",
                       "呵呵", "哈哈", "笑死", "绝了", "啊这",
                       "真的假的", "不会吧", "离谱"]
        if any(kw in content for kw in contrast_kw):
            score += 1

        sender = m.sender_remark or m.sender or "未知"
        # 低活跃用户的金句更有惊喜感
        if user_stats.get(sender, {}).get("message_count", 0) < 5:
            score += 1

        # ── 强降分特征 ──
        # 纯信息转发（含链接、邮箱等）大幅降分
        if "http" in content or "www." in content:
            score -= 5
        # 邮箱格式降分（xxx@xxx.com）
        import re as _re
        if _re.search(r'[\w.]+@[\w.]+\.\w+', content):
            score -= 5
        # 纯感叹/纯符号（缺少观点）
        chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
        if chinese_chars < 15:
            score -= 3
        # 含 emoji 数量过多（>3 视为表情堆砌）
        emoji_count = sum(1 for c in content if ord(c) > 0x1F000)
        if emoji_count > 3:
            score -= 2

        scored_msgs.append((score, m, quote_type))

    scored_msgs.sort(key=lambda x: x[0], reverse=True)
    golden_quotes_list: list = []
    seen = set()
    for _, m, qtype in scored_msgs[:20]:
        content = m.content or ""
        if content[:20] in seen:
            continue
        seen.add(content[:20])
        sender = m.sender_remark or m.sender or "未知"
        reason = REASON_MAP.get(
            qtype, DEFAULT_REASONS[len(golden_quotes_list) % len(DEFAULT_REASONS)]
        )
        golden_quotes_list.append(
            {"content": content, "sender": sender, "reason": reason}
        )
        if len(golden_quotes_list) >= 8:
            break
    return golden_quotes_list


def _compute_quality_dimensions(
    word_counter, text_msgs, chat_pct, image_pct, reply_pct, night_pct, other_pct
) -> list:
    """计算聊天质量维度（氛围分析）— 始终返回固定 5 个维度，百分比总和为 100"""
    # 重新计算互不重叠的百分比：夜间消息同时属于文字消息，需要拆分
    # chat_pct 包含了夜间文字消息，night_pct 是夜间所有类型消息的占比
    # 这里直接用传入的占比做归一化，确保总和为 100
    raw = [chat_pct, image_pct, reply_pct, night_pct, other_pct]
    total_raw = sum(raw)
    if total_raw <= 0:
        total_raw = 1  # 防止除零

    # 归一化到 100，确保 5 个维度都有值
    normalized = [round(r / total_raw * 100) for r in raw]
    # 修正舍入误差，让总和精确为 100
    diff = 100 - sum(normalized)
    # 把误差加到最大的维度上
    if diff != 0:
        max_idx = normalized.index(max(normalized))
        normalized[max_idx] += diff

    dims = [
        {
            "name": "💬 日常闲聊",
            "percentage": normalized[0],
            "comment": f"文字消息占比{normalized[0]}%，群友热情交流中" if normalized[0] > 0 else "暂无文字消息",
            "color": "#e07850",
        },
        {
            "name": "🖼️ 图片分享",
            "percentage": normalized[1],
            "comment": f"图片占比{normalized[1]}%，视觉丰富度{'较高' if normalized[1] > 15 else '适中'}" if normalized[1] > 0 else "暂无图片分享",
            "color": "#d4a853",
        },
        {
            "name": "🔄 互动回复",
            "percentage": normalized[2],
            "comment": f"引用回复占比{normalized[2]}%，互动{'频繁' if normalized[2] > 10 else '适中'}" if normalized[2] > 0 else "暂无互动回复",
            "color": "#4a9b8c",
        },
        {
            "name": "🌙 深夜活跃",
            "percentage": normalized[3],
            "comment": f"凌晨消息占比{normalized[3]}%，{'夜猫子聚集地' if normalized[3] > 10 else '作息规律'}" if normalized[3] > 0 else "作息规律，没有深夜发言",
            "color": "#8b6bb3",
        },
        {
            "name": "📎 其他内容",
            "percentage": normalized[4],
            "comment": f"语音、表情等多元内容，占比{normalized[4]}%" if normalized[4] > 0 else "暂无语音/表情等其他内容",
            "color": "#5b8cd0",
        },
    ]
    return dims


def _build_summary(
    messages,
    total,
    user_stats,
    active_users,
    top_keywords,
    hourly_counts,
    night_pct,
    date_counts,
    word_counter,
    text_msgs,
    msg_words,
) -> dict:
    """生成群聊摘要"""
    total_msgs = len(messages)
    peak_hour = max(hourly_counts, key=hourly_counts.get) if hourly_counts else 12

    sp = [f"本群共{total_msgs}条消息，{len(user_stats)}位成员参与讨论"]
    if active_users:
        tu, ts = active_users[0]
        sp.append(
            f"{tu}以{ts['message_count']}条消息({round(ts['message_count'] / total * 100) if total else 0}%)霸占榜首"
        )
    if top_keywords[:3]:
        sp.append(f"热门话题围绕「{'、'.join(top_keywords[:3])}」展开")
    if peak_hour >= 0:
        for lo, hi, desc in PEAK_HOUR_DESC:
            if lo <= peak_hour < hi:
                sp.append(f"最活跃时段{desc.format(h=peak_hour)}")
                break
    if night_pct > 15:
        sp.append(f"凌晨消息占比{night_pct}%，这群人是不是不睡觉")
    if len(active_users) > 3:
        t3 = sum(s["message_count"] for _, s in active_users[:3])
        t3p = round(t3 / total * 100) if total else 0
        if t3p > 60:
            sp.append(f"TOP3贡献了{t3p}%的消息，话语权高度集中")
        elif t3p > 40:
            sp.append(f"TOP3贡献了{t3p}%的消息，群内生态还算均衡")
    summary_text = "。".join(sp) + "。"

    # 话题聚类 - 复用 word_counter 和 msg_words，避免重复分词
    topic_keywords = {
        kw: {
            "count": c,
            "samples": [m.content for m in text_msgs if kw in (m.content or "")][:3],
        }
        for kw, c in word_counter.most_common(30)
    }

    # 预构建倒排索引：关键词 → 包含该关键词的消息 id 集合
    kw_msg_ids: Dict[str, set] = {}
    for m in text_msgs:
        mid = id(m)
        for w in msg_words.get(mid, []):
            if w in topic_keywords:
                kw_msg_ids.setdefault(w, set()).add(mid)

    topic_clusters = []
    used_kw = set()
    for kw, info in list(topic_keywords.items()):
        if kw in used_kw:
            continue
        cluster = [kw]
        used_kw.add(kw)
        kw_ids = kw_msg_ids.get(kw, set())
        for okw in topic_keywords:
            if okw in used_kw:
                continue
            okw_ids = kw_msg_ids.get(okw, set())
            if len(kw_ids & okw_ids) >= 2:
                cluster.append(okw)
                used_kw.add(okw)
        sample = info["samples"][0][:60] if info["samples"] else ""
        topic_clusters.append(
            {
                "name": kw,
                "description": f"相关词：{'、'.join(cluster[:5])}，讨论{info['count']}次"
                + (f'。如"{sample}…"' if sample else ""),
            }
        )
        if len(topic_clusters) >= 6:
            break

    # 关键观点
    key_points = []
    for m in text_msgs:
        content = m.content or ""
        if len(content) < 15:
            continue
        if any(kw in content for kw in KEYPOINT_OPINION_KW) or (
            len(content) >= 30 and any(kw in content for kw in KEYPOINT_DEPTH_KW)
        ):
            key_points.append(
                {
                    "speaker": m.sender_remark or m.sender or "未知",
                    "point": content[:80] + ("…" if len(content) > 80 else ""),
                }
            )
            if len(key_points) >= 6:
                break

    return {
        "summary": summary_text,
        "topics": topic_clusters,
        "key_points": key_points,
        "action_items": [],
    }


def _build_chat_quality(
    vibe_dims, user_stats, active_users, total, night_pct, date_counts
) -> dict:
    """生成聊天质量锐评"""
    peak_day = max(date_counts, key=date_counts.get) if date_counts else ""
    peak_day_count = date_counts.get(peak_day, 0) if peak_day else 0

    # 标题
    qtp = []
    if vibe_dims:
        vn = vibe_dims[0]["name"]
        qtp.append(vn.split(" ", 1)[-1] if " " in vn else vn)
    quality_title = "·".join(qtp[:3]) if qtp else "群聊数据概览"
    quality_subtitle = f"{len(user_stats)}人参与 · {total}条消息"
    if peak_day:
        quality_subtitle += f" · 峰值日{peak_day}({peak_day_count}条)"

    # 总结金句
    qsp = []
    if vibe_dims:
        vn = vibe_dims[0]["name"]
        qsp.append(f"群聊以{vn.split(' ', 1)[-1] if ' ' in vn else vn}为主旋律")
    if active_users:
        qsp.append(f"{active_users[0][0]}是当之无愧的群内灵魂人物")
    if night_pct > 10:
        qsp.append("深夜依然热闹非凡")
    quality_summary = "，".join(qsp) + "。" if qsp else ""

    return {
        "title": quality_title,
        "subtitle": quality_subtitle,
        "dimensions": vibe_dims,
        "summary": quality_summary,
    }


def rule_based_analysis(messages: List[ChatMessage]) -> Dict[str, Any]:
    """基于规则的群聊分析（无需 AI）"""
    if not messages:
        return copy.deepcopy(EMPTY_RESULT)

    # 1. 收集基础统计数据
    (
        user_stats,
        text_msgs,
        hourly_counts,
        night_count,
        reply_count,
        image_count,
        date_counts,
        sender_msgs,
    ) = _collect_user_stats(messages)

    total = sum(s["message_count"] for s in user_stats.values())
    active_users = sorted(
        user_stats.items(), key=lambda x: x[1]["message_count"], reverse=True
    )

    # 2. 关键词提取
    word_counter, top_keywords, keywords_list, msg_words = _extract_keywords(text_msgs)

    # 3. 用户称号
    user_titles_list = _assign_user_titles(active_users, total)

    # 4. 金句筛选
    golden_quotes_list = _score_golden_quotes(text_msgs, user_stats)

    # 5. 消息类型占比
    total_msgs = len(messages)
    chat_pct = (
        round(sum(1 for m in messages if m.msg_type == "text") / total_msgs * 100)
        if total_msgs
        else 0
    )
    image_pct = round(image_count / total_msgs * 100) if total_msgs else 0
    reply_pct = round(reply_count / total_msgs * 100) if total_msgs else 0
    night_pct = round(night_count / total_msgs * 100) if total_msgs else 0
    other_pct = max(0, 100 - chat_pct - image_pct - reply_pct - night_pct)

    # 6. 质量维度
    vibe_dims = _compute_quality_dimensions(
        word_counter, text_msgs, chat_pct, image_pct, reply_pct, night_pct, other_pct
    )

    # 7. 群聊摘要
    summary_data = _build_summary(
        messages,
        total,
        user_stats,
        active_users,
        top_keywords,
        hourly_counts,
        night_pct,
        date_counts,
        word_counter,
        text_msgs,
        msg_words,
    )

    # 8. 质量锐评
    quality_data = _build_chat_quality(
        vibe_dims, user_stats, active_users, total, night_pct, date_counts
    )

    return {
        "summary": summary_data,
        "keywords": {
            "keywords": keywords_list,
            "hot_topics": [
                {
                    "topic": kw,
                    "frequency": "高" if c > 10 else ("中" if c > 3 else "低"),
                }
                for kw, c in word_counter.most_common(10)
            ],
        },
        "user_titles": {"user_titles": user_titles_list},
        "golden_quotes": {"golden_quotes": golden_quotes_list},
        "chat_quality": quality_data,
    }

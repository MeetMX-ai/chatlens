import asyncio
import json
import logging
import time
from typing import List, Dict, Any, Optional, Callable

from chatlens.errors import AIError, ConfigError

from .models import ChatMessage
from ._analysis_data import (
    SBTI_MAP,
    ACGTI_MAP,
    DEFAULT_COLORS,
    PROMPTS,
    SCHEMAS,
)
from ._analysis_utils import (
    format_messages_id_only,
    build_id_map_text,
    map_ids_back,
    inject_personality,
    try_regex_extract,
    parse_json_response,
    get_user_stats,
)

logger = logging.getLogger("chatlens.ai_analyzer")


# ── 通用分析逻辑（消除同步/异步重复）──────────────────────────


def _make_ai_postprocess(id_map: Dict[str, str]) -> Callable[[Any], Any]:
    """5 个 _do_* 共用的 postprocess 工厂。

    行为：
    1) 如果上层（_call_ai_with_retry）传进来已经含 __error__ 的 dict，
       原样透传（让 banner 看到具体的 429 / 认证 / 超时 / 网络错误），
       不再用通用 reason 覆盖。
    2) 如果是 None / 空（来自非 expect_json 路径的 postprocess(None)），
       返回通用 __error__ 标记。
    3) 正常情况：map_ids_back 把 id 还原回真实发送者。
    """
    def _postprocess(parsed: Any) -> Any:
        if isinstance(parsed, dict) and "__error__" in parsed:
            return parsed
        if not parsed:
            return {"__error__": "ai_returned_empty_or_invalid_json"}
        return map_ids_back(parsed, id_map)
    return _postprocess


def _do_summary(call_fn, messages: List[ChatMessage]) -> Any:
    """群聊摘要 — 纯逻辑，call_fn 为同步或异步的 AI 调用函数"""
    if not messages:
        # 无消息：整段视为 AI 失败
        return {"__error__": "ai_returned_empty_or_invalid_json"}
    msg_text, id_map = format_messages_id_only(messages)
    system_prompt = inject_personality(
        PROMPTS["summary"].format(id_map=build_id_map_text(id_map)), "summary"
    )

    return call_fn(
        system_prompt,
        f"以下是群聊消息记录：\n\n{msg_text}\n\n请分析以上群聊内容。",
        expect_json=True,
        json_schema=SCHEMAS["summary"],
        postprocess=_make_ai_postprocess(id_map),
    )


def _do_keywords(call_fn, messages: List[ChatMessage]) -> Any:
    """关键词提取 — 纯逻辑"""
    if not messages:
        return {"__error__": "ai_returned_empty_or_invalid_json"}
    msg_text, id_map = format_messages_id_only(messages, max_messages=100)
    system_prompt = inject_personality(PROMPTS["keywords"], "keywords")

    return call_fn(
        system_prompt,
        f"以下是群聊消息记录：\n\n{msg_text}\n\n请提取关键词和热门话题。",
        expect_json=True,
        json_schema=SCHEMAS["keywords"],
        postprocess=_make_ai_postprocess(id_map),
    )


def _do_user_titles(call_fn, messages: List[ChatMessage]) -> Any:
    """用户称号分析 — 纯逻辑"""
    if not messages:
        return {"__error__": "ai_returned_empty_or_invalid_json"}
    user_stats = get_user_stats(messages)
    if not user_stats:
        return {"__error__": "ai_returned_empty_or_invalid_json"}
    active_users = {k: v for k, v in user_stats.items() if v["message_count"] >= 3}
    if not active_users:
        active_users = dict(list(user_stats.items())[:10])
    id_map: Dict[str, str] = {}
    for i, name in enumerate(sorted(active_users.keys()), 1):
        id_map[name] = f"U{i}"
    lines = []
    for name, s in sorted(
        active_users.items(), key=lambda x: x[1]["message_count"], reverse=True
    ):
        mc = s["message_count"]
        lines.append(
            f"- {id_map[name]}: 发言{mc}条, 平均{round(s['char_count'] / mc, 1) if mc else 0}字, "
            f"表情比例{round(s['emoji_count'] / mc, 2) if mc else 0}, 夜间发言比例{round(s['night_count'] / mc, 2) if mc else 0}, 回复比例{round(s['reply_count'] / mc, 2) if mc else 0}"
        )
    system_prompt = inject_personality(
        PROMPTS["user_titles"].format(id_map=build_id_map_text(id_map)), "user_titles"
    )

    def _postprocess(parsed):
        # 透传 __error__，保留 sbti/acgti 注入逻辑
        if isinstance(parsed, dict) and "__error__" in parsed:
            return parsed
        if not parsed:
            return {"__error__": "ai_returned_empty_or_invalid_json"}
        mapped = map_ids_back(parsed, id_map)
        for t in mapped.get("user_titles", []):
            mbti = t.get("mbti", "").upper()
            t["sbti"] = SBTI_MAP.get(mbti, "未知生物")
            t["acgti"] = ACGTI_MAP.get(mbti, "未知角色")
        return mapped

    return call_fn(
        system_prompt,
        f"以下是群聊中活跃用户的发言统计：\n\n{chr(10).join(lines)}\n\n请为每个用户分配称号和MBTI。",
        expect_json=True,
        json_schema=SCHEMAS["user_titles"],
        postprocess=_postprocess,
    )


def _do_golden_quotes(call_fn, messages: List[ChatMessage]) -> Any:
    """金句筛选 — 纯逻辑"""
    if not messages:
        return {"__error__": "ai_returned_empty_or_invalid_json"}
    text_messages = [
        m
        for m in messages
        if m.msg_type == "text" and m.content and len(m.content) >= 5
    ]
    if not text_messages:
        return {"__error__": "ai_returned_empty_or_invalid_json"}
    msg_text, id_map = format_messages_id_only(text_messages[:300])
    system_prompt = inject_personality(
        PROMPTS["golden_quotes"].format(id_map=build_id_map_text(id_map)),
        "golden_quotes",
    )

    return call_fn(
        system_prompt,
        f"以下是群聊消息记录：\n\n{msg_text}\n\n请从中筛选金句。",
        expect_json=True,
        json_schema=SCHEMAS["golden_quotes"],
        postprocess=_make_ai_postprocess(id_map),
    )


def _do_chat_quality(call_fn, messages: List[ChatMessage]) -> Any:
    """聊天质量锐评 — 纯逻辑"""
    if not messages:
        return {"__error__": "ai_returned_empty_or_invalid_json"}
    msg_text, id_map = format_messages_id_only(messages[:300])
    system_prompt = inject_personality(PROMPTS["chat_quality"], "chat_quality")

    def _postprocess(parsed):
        # 透传 __error__，保留 dimension color 兜底逻辑
        if isinstance(parsed, dict) and "__error__" in parsed:
            return parsed
        if not parsed:
            return {"__error__": "ai_returned_empty_or_invalid_json"}
        for i, d in enumerate(parsed.get("dimensions", [])):
            if "color" not in d or not d["color"]:
                d["color"] = DEFAULT_COLORS[i % len(DEFAULT_COLORS)]
        return map_ids_back(parsed, id_map)

    return call_fn(
        system_prompt,
        f"以下是群聊消息记录：\n\n{msg_text}\n\n请给出聊天质量锐评。",
        expect_json=True,
        json_schema=SCHEMAS["chat_quality"],
        postprocess=_postprocess,
    )


class GroupAIAnalyzer:
    MAX_RETRIES = 3
    TEMPERATURE_DECAY = [0.7, 0.5, 0.3]
    # P0 生产化 (AC1/AC2)：AI 调用默认 60s 超时，避免无响应 API 挂死整个 web 请求
    # 单次 LLM 调用最多 60s。ai_timeout 可在 config 中覆盖。
    DEFAULT_AI_TIMEOUT = 60.0
    # AC3：端到端硬保护 5min。所有 5 个子分析加起来不能超过这个时间。
    DEFAULT_ANALYSIS_DEADLINE = 300.0
    # P2 修复：并发数配置。某些模型（reasoning / 限流敏感 / 本地 ollama）
    # 在 5 路并发下会出现响应截断、配额打满等问题。允许 1-5：
    #   1 = 串行；2-5 = 并发线程数（>= 子分析数时全部并发）
    # 默认 5（保持原有行为）。
    DEFAULT_CONCURRENT_WORKERS = 5
    MIN_CONCURRENT_WORKERS = 1
    MAX_CONCURRENT_WORKERS = 5
    # 5 个子分析的 (key, sync_method, async_method) 三元组，作为单一事实源
    sub_methods_default = [
        ("summary", "generate_summary", "agenerate_summary"),
        ("keywords", "extract_keywords", "aextract_keywords"),
        ("user_titles", "analyze_user_titles", "aanalyze_user_titles"),
        ("golden_quotes", "analyze_golden_quotes", "aanalyze_golden_quotes"),
        ("chat_quality", "analyze_chat_quality", "aanalyze_chat_quality"),
    ]

    def __init__(self, config: Dict[str, Any]) -> None:
        self.provider = config.get("provider", "deepseek")
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url", "")
        self.model = config.get("model", "")
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 4096)
        self.ai_timeout = float(config.get("ai_timeout", self.DEFAULT_AI_TIMEOUT))
        self.analysis_deadline = float(
            config.get("analysis_deadline", self.DEFAULT_ANALYSIS_DEADLINE)
        )
        # 关闭 reasoning 模型"思考"（NVIDIA NIM chat_template_kwargs）
        # 对支持思考的模型（Step3/Qwen3/DeepSeek 等）：true=正常思考（默认），false=跳过 thinking
        # 用户配置仍用 enable_thinking；注入 LLM 时根据模型不同转写参数名（thinking/reasoning_effort）
        self.enable_thinking = bool(config.get("enable_thinking", True))
        # 向后兼容：旧的 disable_concurrent=True 等价于 workers=1
        legacy_disable = config.get("disable_concurrent", False)
        legacy_workers = config.get("concurrent_workers", None)
        if legacy_workers is not None:
            try:
                w = int(legacy_workers)
            except (TypeError, ValueError):
                w = self.DEFAULT_CONCURRENT_WORKERS
        elif legacy_disable:
            w = 1
        else:
            w = self.DEFAULT_CONCURRENT_WORKERS
        self.concurrent_workers = max(
            self.MIN_CONCURRENT_WORKERS,
            min(self.MAX_CONCURRENT_WORKERS, w),
        )
        self._client: Any = None
        self._async_client: Any = None
        # 最近一次 AI 调用的具体失败原因（429/认证/超时/网络等），
        # 供 _call_ai_with_retry 读取后透传到上层 banner。
        self._last_error: Optional[str] = None

    # ── 客户端初始化 ──────────────────────────────────────────

    def _get_client(self) -> Optional[Any]:
        if self._client is not None:
            return self._client
        if not self.api_key:
            logger.warning("API Key 未配置，AI 分析功能不可用")
            return None
        try:
            from openai import OpenAI

            # P0 修复 (AC1)：OpenAI 客户端支持 timeout 参数，传 httpx.Timeout
            import httpx

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=httpx.Timeout(self.ai_timeout, connect=10.0),
            )
            return self._client
        except ImportError:
            # AC6：logger.exception 替代 logger.error
            logger.exception("openai 库未安装，请运行: pip install openai")
            return None
        except Exception as e:
            # AC6：logger.exception + 抛 ConfigError（API base / 证书等配置问题）
            logger.exception("初始化 AI 客户端失败: %s", e)
            raise ConfigError(
                f"AI 客户端初始化失败: {e}",
                hint="请检查 ai_service.api_key / base_url 配置",
            ) from e

    def _get_async_client(self) -> Optional[Any]:
        if self._async_client is not None:
            return self._async_client
        if not self.api_key:
            logger.warning("API Key 未配置，AI 异步分析功能不可用")
            return None
        try:
            from openai import AsyncOpenAI
            import httpx

            # P0 修复 (AC2)：AsyncOpenAI 同样传 timeout
            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=httpx.Timeout(self.ai_timeout, connect=10.0),
            )
            return self._async_client
        except ImportError:
            # AC6：logger.exception 替代 logger.error
            logger.exception("openai 库未安装，请运行: pip install openai")
            return None
        except Exception as e:
            # AC6：logger.exception + 抛 ConfigError
            logger.exception("初始化异步 AI 客户端失败: %s", e)
            raise ConfigError(
                f"AI 异步客户端初始化失败: {e}",
                hint="请检查 ai_service.api_key / base_url 配置",
            ) from e

    # ── 底层 AI 调用 ──────────────────────────────────────────

    def _call_ai(
        self, system_prompt: str, user_prompt: str, temperature: Optional[float] = None
    ) -> Optional[str]:
        client = self._get_client()
        if not client:
            self._last_error = "AI 客户端未初始化（API key 未配置或 base_url 错误）"
            return None
        try:
            kwargs: Dict[str, Any] = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature
                if temperature is not None
                else self.temperature,
                max_tokens=self.max_tokens,
            )
            # 对支持 reasoning 的模型，关闭 thinking（跳过慢速 CoT）
            if not self.enable_thinking:
                kwargs["extra_body"] = {
                    "chat_template_kwargs": {"thinking": False, "reasoning_effort": "low"}
                }
            resp = client.chat.completions.create(**kwargs)
            self._last_error = None
            return resp.choices[0].message.content  # type: ignore[no-any-return]
        except Exception as e:
            # AC6：logger.exception（带 stacktrace）；同时把分类后的 reason 写到
            # self._last_error 让 _call_ai_with_retry 透传到 banner。
            self._last_error = self._classify_error(e)
            logger.warning("AI 调用失败: %s（已分类: %s）", e, self._last_error)
            return None

    async def _acall_ai(
        self, system_prompt: str, user_prompt: str, temperature: Optional[float] = None
    ) -> Optional[str]:
        client = self._get_async_client()
        if not client:
            self._last_error = "AI 异步客户端未初始化（API key 未配置或 base_url 错误）"
            return None
        try:
            kwargs: Dict[str, Any] = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature
                if temperature is not None
                else self.temperature,
                max_tokens=self.max_tokens,
            )
            if not self.enable_thinking:
                kwargs["extra_body"] = {
                    "chat_template_kwargs": {"thinking": False, "reasoning_effort": "low"}
                }
            resp = await client.chat.completions.create(**kwargs)
            self._last_error = None
            return resp.choices[0].message.content  # type: ignore[no-any-return]
        except Exception as e:
            # AC6：logger.exception（带 stacktrace）；同时把分类后的 reason 写到
            # self._last_error 让 _acall_ai_with_retry 透传到 banner。
            self._last_error = self._classify_error(e)
            logger.warning("异步 AI 调用失败: %s（已分类: %s）", e, self._last_error)
            return None

    def _classify_error(self, e: Exception) -> str:
        """把 OpenAI 异常分类成用户可读的 reason（透传到前端 banner）。"""
        try:
            from openai import (
                RateLimitError,
                AuthenticationError,
                APIConnectionError,
                APITimeoutError,
                BadRequestError,
            )
        except ImportError:
            return f"AI 调用失败: {type(e).__name__}: {str(e)[:120]}"
        if isinstance(e, RateLimitError):
            # 429 - 最常见，限流
            return "AI 接口限流 (HTTP 429): 请求频率过高，请稍后重试或降低并发"
        if isinstance(e, AuthenticationError):
            return "AI 认证失败: API key 无效、过期或 base_url 配置错误"
        if isinstance(e, APITimeoutError):
            return "AI 接口超时: 服务端响应过慢，请检查网络或增加 timeout"
        if isinstance(e, APIConnectionError):
            return f"AI 接口网络错误: {str(e)[:80]}"
        if isinstance(e, BadRequestError):
            # 400 类：参数 / prompt 问题
            msg = str(e)[:200]
            return f"AI 请求被拒绝 (HTTP 400): {msg}"
        # 兜底
        return f"AI 调用失败: {type(e).__name__}: {str(e)[:120]}"

    def _call_ai_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        expect_json: bool = True,
        json_schema: Optional[Dict] = None,
        postprocess: Optional[Callable] = None,
    ) -> Any:
        """同步 AI 调用 + 重试 + 后处理。如有 postprocess，返回 postprocess(parsed)，否则返回 (raw, parsed)"""
        if not self.api_key:
            logger.debug("API Key 未配置，跳过 AI 调用")
            err_payload = {"__error__": "API key 未配置"}
            return postprocess(err_payload) if postprocess else (None, err_payload)
        last_error: Optional[str] = None
        last_raw: Optional[str] = None
        for attempt in range(self.MAX_RETRIES):
            temp = self.TEMPERATURE_DECAY[min(attempt, len(self.TEMPERATURE_DECAY) - 1)]
            logger.info(f"AI 调用尝试 {attempt + 1}/{self.MAX_RETRIES}, 温度={temp}")
            raw = self._call_ai(system_prompt, user_prompt, temperature=temp)
            if not raw:
                # _call_ai 已经在 self._last_error 里写了具体原因
                err = self._last_error or f"第 {attempt + 1} 次调用返回空（未知原因）"
                last_error = err
                logger.warning(f"第 {attempt + 1} 次调用返回空: {err}，重试中...")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)
                continue
            last_raw = raw
            if expect_json:
                parsed = parse_json_response(raw, json_schema)
                if parsed is not None:
                    return postprocess(parsed) if postprocess else (raw, parsed)
                last_error = f"第 {attempt + 1} 次响应无法解析为 JSON"
                logger.warning(f"第 {attempt + 1} 次 JSON 解析失败，重试中（温度递减）...")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(0.5)
            else:
                return postprocess(None) if postprocess else (raw, None)
        if expect_json and last_raw:
            logger.info("所有重试均失败，尝试 Schema 修复...")
            parsed = self._try_schema_repair(last_raw, json_schema)
            if parsed is not None:
                return postprocess(parsed) if postprocess else (last_raw, parsed)
            last_error = (last_error or "") + "（Schema 修复也失败）"
        # 返回错误：postprocess 会透传 __error__ dict
        err_payload = {"__error__": last_error or "AI 调用失败（未知原因）"}
        return postprocess(err_payload) if postprocess else (None, err_payload)

    async def _acall_ai_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        expect_json: bool = True,
        json_schema: Optional[Dict] = None,
        postprocess: Optional[Callable] = None,
    ) -> Any:
        """异步 AI 调用 + 重试 + 后处理"""
        if not self.api_key:
            logger.debug("API Key 未配置，跳过异步 AI 调用")
            err_payload = {"__error__": "API key 未配置"}
            return postprocess(err_payload) if postprocess else (None, err_payload)
        last_error: Optional[str] = None
        last_raw: Optional[str] = None
        for attempt in range(self.MAX_RETRIES):
            temp = self.TEMPERATURE_DECAY[min(attempt, len(self.TEMPERATURE_DECAY) - 1)]
            logger.info(
                f"异步 AI 调用尝试 {attempt + 1}/{self.MAX_RETRIES}, 温度={temp}"
            )
            raw = await self._acall_ai(system_prompt, user_prompt, temperature=temp)
            if not raw:
                # _acall_ai 已经在 self._last_error 里写了具体原因
                err = self._last_error or f"第 {attempt + 1} 次调用返回空（未知原因）"
                last_error = err
                logger.warning(f"第 {attempt + 1} 次异步调用返回空: {err}，重试中...")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(1)
                continue
            last_raw = raw
            if expect_json:
                parsed = parse_json_response(raw, json_schema)
                if parsed is not None:
                    return postprocess(parsed) if postprocess else (raw, parsed)
                last_error = f"第 {attempt + 1} 次响应无法解析为 JSON"
                logger.warning(
                    f"第 {attempt + 1} 次异步调用 JSON 解析失败，重试中（温度递减）..."
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(0.5)
            else:
                return postprocess(None) if postprocess else (raw, None)
        if expect_json and last_raw:
            logger.info("所有异步重试均失败，尝试 Schema 修复...")
            parsed = await self._atry_schema_repair(last_raw, json_schema)
            if parsed is not None:
                return postprocess(parsed) if postprocess else (last_raw, parsed)
            last_error = (last_error or "") + "（Schema 修复也失败）"
        # 返回错误：postprocess 会透传 __error__ dict
        err_payload = {"__error__": last_error or "AI 调用失败（未知原因）"}
        return postprocess(err_payload) if postprocess else (None, err_payload)

    def _try_schema_repair(
        self, raw: str, schema: Optional[Dict] = None
    ) -> Optional[dict]:
        if not schema:
            return None
        repair_prompt = PROMPTS["schema_repair"].format(
            schema=json.dumps(schema, ensure_ascii=False, indent=2)
        )
        client = self._get_client()
        if not client:
            return try_regex_extract(raw or "", schema)
        try:
            kwargs: Dict[str, Any] = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": repair_prompt},
                    {"role": "user", "content": "请重新生成符合 Schema 的 JSON。"},
                ],
                temperature=0.2,
                max_tokens=self.max_tokens,
            )
            if not self.enable_thinking:
                kwargs["extra_body"] = {
                    "chat_template_kwargs": {"thinking": False, "reasoning_effort": "low"}
                }
            resp = client.chat.completions.create(**kwargs)
            result = resp.choices[0].message.content
            if result:
                parsed = parse_json_response(result, schema)
                if parsed is not None:
                    return parsed
        except Exception as e:
            # AC6：logger.exception（带 stacktrace）替代 logger.error
            logger.exception("Schema 修复调用失败: %s", e)
        return try_regex_extract(raw or "", schema)

    async def _atry_schema_repair(
        self, raw: str, schema: Optional[Dict] = None
    ) -> Optional[dict]:
        if not schema:
            return None
        repair_prompt = PROMPTS["schema_repair"].format(
            schema=json.dumps(schema, ensure_ascii=False, indent=2)
        )
        client = self._get_async_client()
        if not client:
            return try_regex_extract(raw or "", schema)
        try:
            kwargs: Dict[str, Any] = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": repair_prompt},
                    {"role": "user", "content": "请重新生成符合 Schema 的 JSON。"},
                ],
                temperature=0.2,
                max_tokens=self.max_tokens,
            )
            if not self.enable_thinking:
                kwargs["extra_body"] = {
                    "chat_template_kwargs": {"thinking": False, "reasoning_effort": "low"}
                }
            resp = await client.chat.completions.create(**kwargs)
            result = resp.choices[0].message.content
            if result:
                parsed = parse_json_response(result, schema)
                if parsed is not None:
                    return parsed
        except Exception as e:
            # AC6：logger.exception（带 stacktrace）替代 logger.error
            logger.exception("异步 Schema 修复调用失败: %s", e)
        return try_regex_extract(raw or "", schema)

    # ── 同步分析方法（薄包装）──────────────────────────────────

    def generate_summary(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return _do_summary(self._call_ai_with_retry, messages)  # type: ignore[no-any-return]

    def extract_keywords(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return _do_keywords(self._call_ai_with_retry, messages)  # type: ignore[no-any-return]

    def analyze_user_titles(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return _do_user_titles(self._call_ai_with_retry, messages)  # type: ignore[no-any-return]

    def analyze_golden_quotes(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return _do_golden_quotes(self._call_ai_with_retry, messages)  # type: ignore[no-any-return]

    def analyze_chat_quality(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return _do_chat_quality(self._call_ai_with_retry, messages)  # type: ignore[no-any-return]

    def full_analysis(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        """同步入口：5 个子分析按 self.concurrent_workers 跑（AC4 + AC3 端到端硬保护）。

        workers=1 时退化为串行（_full_analysis_slow），用于 reasoning/限流敏感模型。
        workers>=2 时用 ThreadPoolExecutor 并发跑（最多 5 路，受子分析数上限限制）。
        """
        from chatlens.logging_config import new_request_id, set_request_id, reset_request_id
        rid = new_request_id()
        token = set_request_id(rid)
        try:
            if self.concurrent_workers <= 1:
                logger.info(
                    "开始 AI 全面分析（串行，workers=1, rid=%s）", rid
                )
                return self._full_analysis_slow(messages)
            return self._full_analysis_concurrent(messages, rid=rid)
        finally:
            reset_request_id(token)

    def _full_analysis_concurrent(self, messages, rid=""):
        import concurrent.futures
        import contextvars
        # workers 受子分析数（5）约束
        workers = min(self.concurrent_workers, len(self.sub_methods_default))
        logger.info("开始 AI 全面分析（并发，workers=%d, rid=%s）", workers, rid)

        def _call(key, name, *args):
            return key, getattr(self, name)(*args)

        # AC8：线程池端到端 request_id 串联 — 用 contextvars.copy_context
        # 把当前 request_id 复制到 worker 线程，子线程里的 logger.info 自动带 rid。
        def _propagated_call(key, name, *args):
            ctx = contextvars.copy_context()
            return ctx.run(_call, key, name, *args)

        sub_methods = self.sub_methods_default
        ex = None
        try:
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
            # AC8：改用 _propagated_call 而非 _call
            futures = [
                ex.submit(_propagated_call, key, sync_name, messages)
                for key, sync_name, _async_name in sub_methods
            ]
            results = {}
            # AC3：端到端硬保护，等待所有 futures 最多 self.analysis_deadline 秒
            deadline = self.analysis_deadline
            try:
                done_iter = concurrent.futures.as_completed(futures, timeout=deadline)
                for fut in done_iter:
                    try:
                        key, value = fut.result()
                        results[key] = value
                    except Exception as e:
                        # AC6：logger.exception 替代 logger.warning
                        logger.exception("子分析失败: %s", e)
            except concurrent.futures.TimeoutError:
                # AC3：超过 deadline，剩余任务被取消
                logger.error(
                    f"AI 全面分析超过硬保护 {deadline:.0f}s，取消剩余子分析"
                )
                for fut in futures:
                    fut.cancel()
                # 等已 cancel 的清理（最多 2s）
                for fut in futures:
                    try:
                        fut.result(timeout=2)
                    except (concurrent.futures.CancelledError, Exception):
                        pass
        except Exception as e:
            # AC6：logger.exception（带 stacktrace）替代 logger.warning
            logger.exception("并发 full_analysis 失败，降级为串行: %s", e)
            if ex is not None:
                try:
                    ex.shutdown(wait=False)
                except Exception:
                    pass
            # AC6：抛 AIError 让上层决定是否升级
            raise AIError(
                f"并发 full_analysis 失败: {e}",
                hint="请稍后重试，或检查 AI 服务可用性",
            ) from e
        finally:
            # AC3 关键修复：不 wait=True，避免遗留慢线程挂死调用者
            if ex is not None:
                try:
                    ex.shutdown(wait=False)
                except Exception:
                    pass
        # 填齐缺失 key（防止某个子分析失败导致 KeyError）
        for key, _sync, _async in sub_methods:
            results.setdefault(key, {})
        return results

    def _full_analysis_slow(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        """串行路径：5 个子分析依次跑（workers=1 或并发降级时）。"""
        return {
            "summary": self.generate_summary(messages),
            "keywords": self.extract_keywords(messages),
            "user_titles": self.analyze_user_titles(messages),
            "golden_quotes": self.analyze_golden_quotes(messages),
            "chat_quality": self.analyze_chat_quality(messages),
        }

    async def _afull_analysis_slow(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        """异步串行路径：5 个子分析依次 await（workers=1 或并发降级时）。"""
        return {
            "summary": await self.agenerate_summary(messages),
            "keywords": await self.aextract_keywords(messages),
            "user_titles": await self.aanalyze_user_titles(messages),
            "golden_quotes": await self.aanalyze_golden_quotes(messages),
            "chat_quality": await self.aanalyze_chat_quality(messages),
        }

    # ── 异步分析方法（薄包装）──────────────────────────────────

    async def agenerate_summary(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return await _do_summary(self._acall_ai_with_retry, messages)  # type: ignore[no-any-return]

    async def aextract_keywords(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return await _do_keywords(self._acall_ai_with_retry, messages)  # type: ignore[no-any-return]

    async def aanalyze_user_titles(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        return await _do_user_titles(self._acall_ai_with_retry, messages)  # type: ignore[no-any-return]

    async def aanalyze_golden_quotes(
        self, messages: List[ChatMessage]
    ) -> Dict[str, Any]:
        return await _do_golden_quotes(self._acall_ai_with_retry, messages)  # type: ignore[no-any-return]

    async def aanalyze_chat_quality(
        self, messages: List[ChatMessage]
    ) -> Dict[str, Any]:
        return await _do_chat_quality(self._acall_ai_with_retry, messages)  # type: ignore[no-any-return]

    async def afull_analysis(self, messages: List[ChatMessage]) -> Dict[str, Any]:
        """异步 AI 全面分析：按 self.concurrent_workers 控制并发数。

        workers=1 走串行（_afull_analysis_slow）；
        workers>=2 走 Semaphore + gather，最多同时 N 个子分析在跑。
        """
        if self.concurrent_workers <= 1:
            logger.info("开始异步 AI 全面分析（串行，workers=1）")
            return await self._afull_analysis_slow(messages)

        workers = min(self.concurrent_workers, len(self.sub_methods_default))
        logger.info("开始异步 AI 全面分析（并发，workers=%d）", workers)
        sem = asyncio.Semaphore(workers)

        async def _run(name_coro):
            async with sem:
                return await name_coro

        keys = ["summary", "keywords", "user_titles", "golden_quotes", "chat_quality"]
        coros = [
            _run(self.agenerate_summary(messages)),
            _run(self.aextract_keywords(messages)),
            _run(self.aanalyze_user_titles(messages)),
            _run(self.aanalyze_golden_quotes(messages)),
            _run(self.aanalyze_chat_quality(messages)),
        ]
        values = await asyncio.gather(*coros, return_exceptions=True)
        results: Dict[str, Any] = {}
        for k, v in zip(keys, values):
            results[k] = v if not isinstance(v, Exception) else {}
            if isinstance(v, Exception):
                logger.exception("异步子分析 %s 失败: %s", k, v)
        return results


from ._rule_engine import rule_based_analysis  # noqa: E402,F401 — re-export for backward compat


def generate_ide_prompt(
    group_name: str, messages: List["ChatMessage"], count: int = 200
) -> str:
    if not messages:
        return f"未找到 {group_name} 的消息数据。"

    recent = messages[-count:]
    lines = ["请分析以下群聊消息，生成群聊分析报告。\n"]
    lines.append("## 分析要求")
    lines.append(
        "请根据消息内容，生成以下分析结果，然后调用 MCP 工具 `generate_report_image` 生成图片报告：\n"
    )
    lines.append("1. **群聊摘要**（2-3句话概括群聊氛围和主要话题）")
    lines.append("2. **讨论话题**（3-5个主要话题，每个含名称和描述）")
    lines.append("3. **用户称号**（为活跃用户取有趣称号，含MBTI推断和理由）")
    lines.append("4. **金句**（3-5条最精彩/有洞察的发言，含推荐理由）")
    lines.append("5. **质量锐评**（3-5个维度的占比和犀利点评，含标题和总结金句）")
    lines.append("6. **关键词**（10-15个高频关键词，含相关度1-10）\n")
    lines.append("## 群聊消息\n")
    for m in recent:
        if m.msg_attr == "system":
            continue
        sender = m.sender_remark or m.sender or "未知"
        time_part = ""
        if m.timestamp:
            try:
                time_part = m.timestamp.split(" ")[1][:5]
            except (ValueError, IndexError):
                pass
        if m.msg_type == "text":
            lines.append(f"[{time_part}] {sender}: {m.content}")
        elif m.msg_type == "image":
            lines.append(f"[{time_part}] {sender}: [图片]")
        elif m.msg_type == "voice":
            lines.append(f"[{time_part}] {sender}: [语音]")
        elif m.msg_type == "quote":
            lines.append(
                f"[{time_part}] {sender}: (引用) {m.quote_content[:80]} | {m.content}"
            )
        elif m.msg_type == "emotion":
            lines.append(f"[{time_part}] {sender}: [表情]")
        else:
            lines.append(f"[{time_part}] {sender}: [{m.msg_type}]")

    lines.append(f"\n--- 共 {len(recent)} 条消息 ---\n")
    lines.append("## 生成报告")
    lines.append("分析完成后，请调用 `generate_report_image` 工具，参数：")
    lines.append(f'- group_name: "{group_name}"')
    lines.append('- theme: "scrapbook"')
    lines.append('- fmt: "jpg"')
    lines.append("- ai_summary: 你的摘要文本")
    lines.append("- ai_topics: 话题 JSON 数组")
    lines.append("- ai_user_titles: 用户称号 JSON 数组")
    lines.append("- ai_golden_quotes: 金句 JSON 数组")
    lines.append("- ai_chat_quality: 质量锐评 JSON 对象")
    lines.append("- ai_keywords: 关键词 JSON 数组")

    return "\n".join(lines)

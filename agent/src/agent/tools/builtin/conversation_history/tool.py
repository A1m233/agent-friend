"""``ConversationHistoryTool``：对 LLM 暴露的"回忆过去聊过的事"能力。

本工具实现 :class:`agent.tools.Tool` Protocol——作为 005 已建立的工具调用机制
的**第二个**具体落地（第一个是 ``web_search``）。

LLM 通过本工具主动检索过往对话；返回结果是**纯文本日记叙事风格**——不暴露
``session_id`` / ``event_type`` / ``role`` 等 schema 字眼，避免 LLM 脱口而出
"我查询了会话记录"之类技术化表达（详见 020 requirement §4.2）。

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.1 / §4.4。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar, Literal, cast

from memory.search import MemorySearchService, QueryAnalyzer, SaidBy, SearchRequest, TimeRange

from memory import DEFAULT_OWNER_USER_ID

from ....memory_search import SessionHistorySource
from ....sessions.catalog import SessionCatalog, SessionResolution
from ....sessions.store import SessionStore
from ...protocol import ToolResult
from .render import format_evidence
from .time_parser import parse_time_expression

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50
_MIN_LIMIT = 1
_CURRENT_SESSION_ID_ARG = "__agent_friend_current_session_id"
_CURRENT_TURN_START_INDEX_ARG = "__agent_friend_current_turn_start_index"
_CURRENT_PERSONA_ID_ARG = "__agent_friend_current_persona_id"
_ORIGINAL_USER_INPUT_ARG = "__agent_friend_original_user_input"
_RUNTIME_NOW_ARG = "__agent_friend_runtime_now"
_TIME_EXPR_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?"
    r"|\d+\s*(?:小时|天|周|个月|年)前|今天|昨天|前天|本周|上周|本月|上月|今年|去年|前年"
)
_SUMMARY_MARKERS = (
    "聊了什么",
    "聊过什么",
    "聊了啥",
    "聊过啥",
    "说了什么",
    "说过什么",
    "总结",
    "主要聊",
    "有啥",
)
_HISTORY_MARKERS = (
    "翻一下",
    "翻翻",
    "找一下",
    "查一下",
    "看看",
    "之前聊",
    "以前聊",
    "上次聊",
    "最近聊",
    "聊了啥",
    "聊过啥",
    "聊过",
    "说过",
    "提过",
    "我之前",
    "哪个会话",
    "这个会话",
)


def _default_now() -> datetime:
    """系统本地时区的当前 aware datetime。"""
    return datetime.now().astimezone()


class ConversationHistoryTool:
    """对 LLM 暴露的"回忆过去对话"工具，实现 :class:`agent.tools.Tool` Protocol。

    048 M48.1 起依赖统一 :class:`MemorySearchService`；旧 ``store`` 参数保留为
    兼容入口，并会在构造期包装成只含 ``SessionHistorySource`` 的 service。

    Args:
        store: 020 兼容参数；未传 ``search_service`` 时用于构造历史 source。
        search_service: bridge / 新调用方注入的统一搜索 service。
        clock: 注入"现在"，便于单测；默认系统本地时区 ``datetime.now().astimezone()``。
    """

    name: ClassVar[str] = "recall_past_chats"
    description: ClassVar[str] = (
        "回忆过去和这位用户聊过的事。\n\n"
        "**何时使用**：\n"
        "- 用户提及之前的对话内容（如「我们上次聊到 X」、「你之前说过的 Y 怎么样了」）\n"
        "- 用户问及一段时间前发生的事，当前上下文里找不到 / 你记不清楚\n"
        "- 用户按会话列表中可见的标题，要求查询或总结该会话；工具可以读取本机当前"
        "可访问的多个持久化会话，不限于当前会话\n"
        "- 你需要确认是否聊过某个话题、说过某些话、做过某个约定或推荐\n"
        "- 用户质疑你前后说法不一致、刚才不是说不记得、是不是骗了他，或要求你"
        "只说现在能确认什么；当前上下文里看不到被质疑内容时，先用本工具核对历史\n\n"
        "**何时不要使用**：\n"
        "- 信息就在当前对话上下文里可见；直接基于当前上下文回答，不要说成翻了历史\n"
        "- 用户问的是通用知识、外部事实或实时信息，且没有要求核对「我们之前聊过什么」\n"
        "- 缓存、数据库、同步等词出现在技术问题中，但并不指向用户和你的过去交流\n"
        "- 你想证明外部事实是否真实；本工具只能证明过去对话里是否出现过\n\n"
        "**参数来源与证据边界**：\n"
        "- query 来自用户提到的主题、对象、关键词或你要核对的历史说法\n"
        "- session_title 来自用户看到的会话标题；不要传 session_id\n"
        "- since / until 只在用户给出明确时间边界时使用；「最近」「这阵子」不要硬塞进 since\n"
        "- said_by 只在用户明确关心「你说过」或「我说过」时使用\n"
        "- 返回结果只代表过去对话片段，不代表外部事实正确，也不包含工具调用记录、"
        "persona 切换等系统事件\n\n"
        "**结果如何表达**：\n"
        "- 有命中时，可以自然说「我看了下之前聊到的」；只把片段字面支持的内容说成聊过\n"
        "- 没命中时，直接说没翻到；不要用通识、训练数据、用户暗示或 web_search 补成历史\n"
        "- 会话名有歧义时用候选标题请用户确认；一次没命中不代表不能跨会话\n"
        "- 不要对用户说工具名、查询参数、schema、session_id、event_type、role 等技术字眼"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "session_title": {
                "type": "string",
                "description": "要查询的会话展示标题或足够明确的标题片段。可选；不要传内部 id。",
            },
            "query": {
                "type": "string",
                "description": (
                    "要回忆的关键词。在过去对话内容中模糊匹配（包含即命中、大小写无关）。"
                    "可选；不填则按时间范围返回所有片段。"
                ),
            },
            "since": {
                "type": "string",
                "description": (
                    "回忆的明确时间下界。支持 ISO 8601 日期（如「2026-06-15」）或"
                    "明确自然语言时间（「3 天前」/「上周」/「上月」/「去年」等）。可选。"
                    "如果用户只是泛泛问「最近」/「近期」/「这阵子」聊了什么，不要传 since；"
                    "省略 since 并用 limit 按时间倒序取最近片段。"
                ),
            },
            "until": {
                "type": "string",
                "description": "回忆的时间上界。格式同 since。可选。",
            },
            "said_by": {
                "type": "string",
                "enum": ["you", "me"],
                "description": (
                    "只看用户（you）说的，或只看你自己（me）说的。可选；不填则两者都看。"
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["search", "summary"],
                "description": "找原话/确认是否聊过用 search；概括一段会话用 summary。",
                "default": "search",
            },
            "limit": {
                "type": "integer",
                "description": f"最多返回多少条回忆。默认 {_DEFAULT_LIMIT}，上限 {_MAX_LIMIT}。",
                "default": _DEFAULT_LIMIT,
            },
        },
    }

    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        search_service: MemorySearchService | None = None,
        session_catalog: SessionCatalog | None = None,
        clock: Callable[[], datetime] = _default_now,
    ) -> None:
        if search_service is None:
            if store is None:
                raise ValueError("search_service 和 store 至少提供一个")
            search_service = MemorySearchService(
                [SessionHistorySource(store)],
                analyzer=QueryAnalyzer(clock),
            )
        self._search_service = search_service
        self._session_catalog = session_catalog
        self._clock = clock
        self._analyzer = QueryAnalyzer(clock)

    def plan_explicit_invocation(self, user_input: str) -> dict[str, Any] | None:
        """Build a conservative provider-independent call for explicit history requests."""
        text = user_input.strip()
        if not text or _is_capability_question(text):
            return None
        title_phrase = _extract_session_title_phrase(text)
        resolution = (
            self._session_catalog.resolve(title_phrase)
            if self._session_catalog is not None and title_phrase
            else self._session_catalog.resolve_from_text(text)
            if self._session_catalog is not None
            else SessionResolution("not_found")
        )
        has_history_marker = any(marker in text for marker in _HISTORY_MARKERS)
        if resolution.status == "not_found" and not has_history_marker:
            return None
        if not has_history_marker and resolution.status != "matched":
            return None

        args: dict[str, Any] = {}
        if title_phrase:
            args["session_title"] = title_phrase
        elif resolution.matched is not None:
            args["session_title"] = resolution.matched.display_title
        time_expressions = _extract_time_expressions(text)
        time_expression = time_expressions[0] if time_expressions else None
        if time_expressions:
            args["since"] = time_expressions[0]
            args["until"] = time_expressions[-1]
        mode = "summary" if any(marker in text for marker in _SUMMARY_MARKERS) else "search"
        args["mode"] = mode
        analyzed = self._analyzer.analyze(
            SearchRequest(
                text=text,
                owner_user_id=DEFAULT_OWNER_USER_ID,
                persona_id="",
                mode="explicit",
            )
        )
        retrieval_text = analyzed.retrieval_text
        for expression in time_expressions[1:]:
            retrieval_text = retrieval_text.replace(expression, " ")
        query = _topic_from_user_input(
            retrieval_text,
            title_phrase,
            time_expression,
            mode,
        )
        if query:
            args["query"] = query
        inferred_speaker = _infer_said_by(text)
        if inferred_speaker:
            args["said_by"] = inferred_speaker
        return args

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        query = _opt_str(args, "query")
        original_user_input = _opt_str(args, _ORIGINAL_USER_INPUT_ARG) or ""
        mode_raw = _opt_str(args, "mode") or (
            "summary"
            if any(marker in original_user_input for marker in _SUMMARY_MARKERS)
            else "search"
        )
        if mode_raw not in ("search", "summary"):
            return ToolResult(text='mode 只接受 "search" 或 "summary"。', is_error=True)
        mode = cast(Literal["search", "summary"], mode_raw)

        session_title = _opt_str(args, "session_title")
        if session_title is not None and self._session_catalog is None:
            return ToolResult(
                text=format_evidence(
                    (),
                    _runtime_now(args, self._clock),
                    status="session_not_found",
                    mode=mode,
                ),
                meta={
                    "result_count": 0,
                    "resolution_status": "catalog_unavailable",
                },
            )
        if session_title is None and self._session_catalog is not None and original_user_input:
            inferred = self._session_catalog.resolve_from_text(original_user_input)
        elif session_title is not None and self._session_catalog is not None:
            inferred = self._session_catalog.resolve(session_title)
        else:
            inferred = None
        if inferred is not None and inferred.status == "ambiguous":
            titles = tuple(item.display_title for item in inferred.candidates)
            return ToolResult(
                text=format_evidence(
                    (),
                    _runtime_now(args, self._clock),
                    status="ambiguous_session",
                    candidate_session_titles=titles,
                    mode=mode,
                ),
                meta={"result_count": 0, "resolution_status": "ambiguous"},
            )
        if session_title is not None and inferred is not None and inferred.status == "not_found":
            return ToolResult(
                text=format_evidence(
                    (),
                    _runtime_now(args, self._clock),
                    status="session_not_found",
                    mode=mode,
                ),
                meta={"result_count": 0, "resolution_status": "not_found"},
            )
        matched_session = inferred.matched if inferred is not None else None
        if matched_session is not None and query is not None:
            normalized_query = query.strip().casefold()
            if any(normalized_query == alias for alias in matched_session.aliases):
                query = None
        said_by = _infer_said_by(original_user_input) or _opt_str(args, "said_by")
        if said_by is not None and said_by not in ("you", "me"):
            return ToolResult(
                text=f'said_by 只接受 "you" 或 "me"，收到的是 {said_by!r}。',
                is_error=True,
            )

        limit = _clamp_limit(args.get("limit", _DEFAULT_LIMIT))
        now = _runtime_now(args, self._clock)

        since_str = _opt_str(args, "since")
        until_str = _opt_str(args, "until")
        try:
            since = parse_time_expression(since_str, now, bias="start") if since_str else None
            until = parse_time_expression(until_str, now, bias="end") if until_str else None
            explicit_time_range = (
                TimeRange(since=since, until=until)
                if since is not None or until is not None
                else None
            )
        except ValueError as e:
            return ToolResult(
                text=(f"时间格式没看懂：{e}。可以用「3 天前」/「上周」/「2026-06-15」这种说法。"),
                is_error=True,
            )
        if explicit_time_range is None and original_user_input:
            try:
                explicit_time_range = _time_range_from_original(
                    original_user_input,
                    now,
                    self._analyzer,
                )
            except ValueError as e:
                return ToolResult(
                    text=f"时间范围没看懂：{e}。请换成明确日期或起止时间。",
                    is_error=True,
                    meta={"error_kind": "invalid_constraints"},
                )

        current_turn_boundary = _current_turn_boundary(args)
        normalized_said_by: SaidBy | None = None
        if said_by == "you":
            normalized_said_by = "user"
        elif said_by == "me":
            normalized_said_by = "agent"
        request = SearchRequest(
            text=query or "",
            owner_user_id=DEFAULT_OWNER_USER_ID,
            persona_id=_opt_str(args, _CURRENT_PERSONA_ID_ARG) or "",
            mode="explicit",
            session_id=current_turn_boundary[0] if current_turn_boundary else None,
            current_turn_start_index=(current_turn_boundary[1] if current_turn_boundary else None),
            top_k=limit,
            said_by=normalized_said_by,
            explicit_time_range=explicit_time_range,
            source_scope=(frozenset({"session_history"}) if matched_session is not None else None),
            target_session_ids=(matched_session.session_id,) if matched_session is not None else (),
            retrieval_goal=mode,
        )
        result = self._search_service.search(request)
        if result.failures and not result.evidence:
            return ToolResult(
                text=format_evidence(
                    (),
                    now,
                    status="source_error",
                    session_title=(
                        matched_session.display_title if matched_session is not None else None
                    ),
                    mode=mode,
                    time_scope=_time_scope_text(
                        since_str,
                        until_str,
                        explicit_time_range is not None,
                    ),
                    message="一时翻不到记录了，等下再说吧。",
                ),
                is_error=True,
                meta={
                    "error_kind": "source_error",
                    "result_count": 0,
                    "source_failures": len(result.failures),
                },
            )

        status = "ok" if result.evidence else "no_results"
        text = format_evidence(
            result.evidence,
            now,
            status=status,
            session_title=(matched_session.display_title if matched_session is not None else None),
            mode=mode,
            time_scope=_time_scope_text(since_str, until_str, explicit_time_range is not None),
            truncated=len(result.evidence) >= limit,
        )
        return ToolResult(
            text=text,
            is_error=False,
            meta={
                "result_count": len(result.evidence),
                "intent": result.query.intent,
                "source_failures": len(result.failures),
                "resolution_status": "matched" if matched_session is not None else "unscoped",
                "session_candidate_counts": _session_counts(result.evidence),
            },
        )


def _runtime_now(args: dict[str, Any], clock: Callable[[], datetime]) -> datetime:
    raw = _opt_str(args, _RUNTIME_NOW_ARG)
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is not None:
                return parsed
        except ValueError:
            pass
    return clock()


def _extract_time_expressions(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in _TIME_EXPR_RE.finditer(text))


def _time_range_from_original(
    text: str,
    now: datetime,
    analyzer: QueryAnalyzer,
) -> TimeRange | None:
    expressions = _extract_time_expressions(text)
    if not expressions:
        inferred = analyzer.analyze(
            SearchRequest(
                text=text,
                owner_user_id=DEFAULT_OWNER_USER_ID,
                persona_id="",
                mode="explicit",
            )
        )
        return inferred.time_range
    return TimeRange(
        since=parse_time_expression(expressions[0], now, bias="start"),
        until=parse_time_expression(expressions[-1], now, bias="end"),
    )


def _extract_session_title_phrase(text: str) -> str | None:
    match = re.search(
        r"(?:帮我)?(?:看看|查查|查一下|翻一下)?\s*(.{1,80}?)\s*(?:这个|那个)?会话", text
    )
    if match is None:
        return None
    value = match.group(1).strip(" ，,。.!！?？")
    return value or None


def _topic_from_user_input(
    text: str,
    session_title: str | None,
    time_expression: str | None,
    mode: str,
) -> str | None:
    result = text
    for value in (session_title, time_expression):
        if value:
            result = result.replace(value, " ")
    for phrase in (
        "帮我看看",
        "看看",
        "查一下",
        "翻一下",
        "这个会话",
        "那个会话",
        "会话",
        "里",
        *_SUMMARY_MARKERS,
    ):
        result = result.replace(phrase, " ")
    result = " ".join(result.strip(" ，,。.!！?？").split())
    if mode == "summary" and result in {"", "今天", "昨天", "上周"}:
        return None
    return result or None


def _is_capability_question(text: str) -> bool:
    return any(
        marker in text for marker in ("能不能", "是否能", "可以吗", "支持吗", "是不是能")
    ) and any(marker in text for marker in ("工具", "跨会话", "查会话"))


def _infer_said_by(text: str) -> str | None:
    if any(
        marker in text
        for marker in ("我说过", "我有没有说", "我有没有跟你说", "我提过", "我跟你说")
    ):
        return "you"
    if any(marker in text for marker in ("你说过", "你之前说", "你推荐", "你让我")):
        return "me"
    return None


def _time_scope_text(since: str | None, until: str | None, inferred: bool) -> str | None:
    if since and until and since == until:
        return since
    if since or until:
        return f"{since or '…'} 至 {until or '…'}"
    return "来自用户原始时间约束" if inferred else None


def _session_counts(evidence: tuple[Any, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in evidence:
        session_id = getattr(candidate, "session_id", None)
        if isinstance(session_id, str):
            counts[session_id] = counts.get(session_id, 0) + 1
    return counts


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    """取一个可选 string 参数；非 str / 空白串都视为缺省。"""
    v = args.get(key)
    if not isinstance(v, str):
        return None
    stripped = v.strip()
    return stripped if stripped else None


def _current_turn_boundary(args: dict[str, Any]) -> tuple[str, int] | None:
    """读取工具执行层注入的当前 turn 边界；LLM 不可见、无效则忽略。"""
    session_id = _opt_str(args, _CURRENT_SESSION_ID_ARG)
    raw_idx = args.get(_CURRENT_TURN_START_INDEX_ARG)
    if session_id is None or not isinstance(raw_idx, int) or raw_idx < 0:
        return None
    return session_id, raw_idx


def _clamp_limit(raw: Any) -> int:
    """把外部传入的 limit 钳到 ``[_MIN_LIMIT, _MAX_LIMIT]``。非整数 fallback 到默认值。"""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return max(_MIN_LIMIT, min(_MAX_LIMIT, n))

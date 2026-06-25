"""``ConversationHistoryTool``：对 LLM 暴露的"回忆过去聊过的事"能力。

本工具实现 :class:`agent.tools.Tool` Protocol——作为 005 已建立的工具调用机制
的**第二个**具体落地（第一个是 ``web_search``）。

LLM 通过本工具主动检索过往对话；返回结果是**纯文本日记叙事风格**——不暴露
``session_id`` / ``event_type`` / ``role`` 等 schema 字眼，避免 LLM 脱口而出
"我查询了会话记录"之类技术化表达（详见 020 requirement §4.2）。

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.1 / §4.4。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar

from ....sessions.errors import SessionPersistError
from ....sessions.events import Event
from ....sessions.store import SessionStore
from ...protocol import ToolResult
from .render import Hit, format_hits
from .time_parser import parse_time_expression

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50
_MIN_LIMIT = 1
_CURRENT_SESSION_ID_ARG = "__agent_friend_current_session_id"
_CURRENT_TURN_START_INDEX_ARG = "__agent_friend_current_turn_start_index"


def _default_now() -> datetime:
    """系统本地时区的当前 aware datetime。"""
    return datetime.now().astimezone()


class ConversationHistoryTool:
    """对 LLM 暴露的"回忆过去对话"工具，实现 :class:`agent.tools.Tool` Protocol。

    数据源就一个 = 注入的 :class:`SessionStore`，无 provider 抽象（详见
    020 design §5.2 N-1）。:class:`NullSessionStore` 等空 store 注入时
    ``store.list()`` 返回空，自然走"翻不到"拟人兜底（详见 020 design §5.2 N-4）。

    Args:
        store: 会话存储；本工具读 ``store.list()`` + ``store.load()`` 扫历史 events。
        clock: 注入"现在"，便于单测；默认系统本地时区 ``datetime.now().astimezone()``。
    """

    name: ClassVar[str] = "recall_past_chats"
    description: ClassVar[str] = (
        "回忆过去和这位用户聊过的事。\n\n"
        "**何时使用**：\n"
        "- 用户提及之前的对话内容（如「我们上次聊到 X」、「你之前说过的 Y 怎么样了」）\n"
        "- 用户问及一段时间前发生的事，上文 context 里找不到 / 你记不清楚\n"
        "- 你需要确认是否聊过某个话题、说过某些话\n\n"
        "**返回**：过去对话片段，按相关时间倒序。基于结果用朋友口吻自然提及，"
        "不要复述时间戳、不要暴露查询动作。\n\n"
        "**注意**：本工具只查过去的对话本身（你和用户互相说过的话），"
        "不查工具调用记录、persona 切换等系统事件。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
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
            "limit": {
                "type": "integer",
                "description": f"最多返回多少条回忆。默认 {_DEFAULT_LIMIT}，上限 {_MAX_LIMIT}。",
                "default": _DEFAULT_LIMIT,
            },
        },
    }

    def __init__(
        self,
        store: SessionStore,
        clock: Callable[[], datetime] = _default_now,
    ) -> None:
        self._store = store
        self._clock = clock

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        query = _opt_str(args, "query")
        said_by = _opt_str(args, "said_by")
        if said_by is not None and said_by not in ("you", "me"):
            return ToolResult(
                text=f'said_by 只接受 "you" 或 "me"，收到的是 {said_by!r}。',
                is_error=True,
            )

        limit = _clamp_limit(args.get("limit", _DEFAULT_LIMIT))
        now = self._clock()

        since_str = _opt_str(args, "since")
        until_str = _opt_str(args, "until")
        try:
            since = parse_time_expression(since_str, now, bias="start") if since_str else None
            until = parse_time_expression(until_str, now, bias="end") if until_str else None
        except ValueError as e:
            return ToolResult(
                text=(f"时间格式没看懂：{e}。可以用「3 天前」/「上周」/「2026-06-15」这种说法。"),
                is_error=True,
            )

        try:
            current_turn_boundary = _current_turn_boundary(args)
            hits = self._scan(query, since, until, said_by, limit, current_turn_boundary)
        except SessionPersistError:
            return ToolResult(
                text="一时翻不到记录了，等下再说吧。",
                is_error=True,
            )

        text = format_hits(hits, now)
        return ToolResult(
            text=text,
            is_error=False,
            meta={"result_count": len(hits)},
        )

    def _scan(
        self,
        query: str | None,
        since: datetime | None,
        until: datetime | None,
        said_by: str | None,
        limit: int,
        current_turn_boundary: tuple[str, int] | None = None,
    ) -> list[Hit]:
        """扫描所有 session 的 events，按过滤条件收集 Hit。"""
        summaries = self._store.list()

        relevant = [
            s
            for s in summaries
            if not (until is not None and s.created_at >= until)
            and not (since is not None and s.updated_at < since)
        ]

        needle = query.lower() if query else None
        hits: list[Hit] = []

        for summary in relevant:
            session = self._store.load(summary.session_id)
            cutoff_idx = (
                current_turn_boundary[1]
                if current_turn_boundary is not None
                and summary.session_id == current_turn_boundary[0]
                else None
            )
            prev: Event | None = None
            for idx, ev in enumerate(session.events):
                if cutoff_idx is not None and idx >= cutoff_idx:
                    break

                if ev.type not in ("user_message", "assistant_message"):
                    continue
                content = _recall_content(ev)
                if content is None:
                    continue

                if since is not None and ev.ts < since:
                    prev = ev
                    continue
                if until is not None and ev.ts >= until:
                    break

                is_user = ev.type == "user_message"
                if said_by == "you" and not is_user:
                    prev = ev
                    continue
                if said_by == "me" and is_user:
                    prev = ev
                    continue

                if needle is not None and needle not in content.lower():
                    prev = ev
                    continue

                pair = (
                    prev
                    if (prev is not None and prev.type in ("user_message", "assistant_message"))
                    else None
                )
                hits.append(Hit(matched=ev, pair=pair))
                prev = ev

        hits.sort(key=lambda h: h.matched.ts, reverse=True)
        return hits[:limit]


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    """取一个可选 string 参数；非 str / 空白串都视为缺省。"""
    v = args.get(key)
    if not isinstance(v, str):
        return None
    stripped = v.strip()
    return stripped if stripped else None


def _recall_content(ev: Event) -> str | None:
    """返回可作为 recall 内容的文本；partial / 空 assistant 不算历史发言。"""
    if ev.type == "assistant_message" and ev.payload.get("partial"):
        return None
    content = ev.payload.get("content", "")
    if not isinstance(content, str):
        return None
    stripped = content.strip()
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

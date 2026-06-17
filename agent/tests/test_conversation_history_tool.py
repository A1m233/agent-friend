"""``ConversationHistoryTool.invoke`` 单元测试。

通过 ``_StubStore`` 注入两段会话的 in-memory events，覆盖：

- happy path：query / since / until / said_by / limit 各参数组合
- 失败兜底：时间格式错 / SessionPersistError / 非法 said_by
- limit clamp、空 store
- _scan 的 pair 配对、跨 session 合并按时间倒序

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.1 / §4.4。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent.sessions.errors import SessionNotFoundError, SessionPersistError
from agent.sessions.events import SCHEMA_VERSION, Event
from agent.sessions.session import Session
from agent.sessions.store import SessionSummary
from agent.tools.builtin.conversation_history.tool import ConversationHistoryTool

_TZ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 6, 17, 14, 30, tzinfo=_TZ)  # 周三


def _make_session(
    session_id: str,
    created_at: datetime,
    events_data: list[tuple[str, str, datetime]],
) -> Session:
    """events_data: list of (type, content, ts)"""
    meta_event = Event(
        type="session_meta",
        uuid=session_id,
        ts=created_at,
        payload={
            "schema_version": SCHEMA_VERSION,
            "initial_title": f"session {session_id}",
            "initial_persona": "test",
            "initial_model": "test/m",
        },
    )
    events = [meta_event]
    for ev_type, content, ts in events_data:
        events.append(
            Event(
                type=ev_type,  # type: ignore[arg-type]
                uuid=str(uuid4()),
                ts=ts,
                payload={"content": content},
            )
        )
    return Session.from_events(events)


class _StubStore:
    """In-memory ``SessionStore``——只实现 ``list`` / ``load``。"""

    def __init__(
        self, sessions: list[Session], *, raise_on_list: BaseException | None = None
    ) -> None:
        self._sessions = {s.session_id: s for s in sessions}
        self._raise_on_list = raise_on_list

    def list(self) -> list[SessionSummary]:
        if self._raise_on_list is not None:
            raise self._raise_on_list
        summaries = []
        for s in self._sessions.values():
            # updated_at 用最后一个 event 的 ts
            updated = s.events[-1].ts if s.events else s.created_at
            summaries.append(
                SessionSummary(
                    session_id=s.session_id,
                    title=s.initial_title,
                    created_at=s.created_at,
                    updated_at=updated,
                    persona=s.initial_persona,
                    model=s.initial_model,
                )
            )
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def load(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"not found: {session_id}")
        return self._sessions[session_id]

    # 以下方法不被 ConversationHistoryTool 使用——stub 不实现
    def create(self, session: Session) -> None: ...  # pragma: no cover
    def append_event(self, session_id: str, event: Event) -> None: ...  # pragma: no cover
    def delete(self, session_id: str) -> None: ...  # pragma: no cover
    def latest(self) -> SessionSummary | None: ...  # pragma: no cover


def _two_session_store() -> _StubStore:
    """构造 2 个 session：A 在 5 天前聊日语；B 在 2 天前聊英语。"""
    five_days_ago = _NOW - timedelta(days=5)
    two_days_ago = _NOW - timedelta(days=2)
    session_a = _make_session(
        "sess-a",
        created_at=five_days_ago - timedelta(seconds=10),
        events_data=[
            ("user_message", "学日语很难，背单词总记不住。", five_days_ago),
            (
                "assistant_message",
                "试试不开字幕看动漫，沉浸式比死记好。",
                five_days_ago + timedelta(seconds=30),
            ),
        ],
    )
    session_b = _make_session(
        "sess-b",
        created_at=two_days_ago - timedelta(seconds=10),
        events_data=[
            ("user_message", "最近我学英语呢", two_days_ago),
            ("assistant_message", "Practice makes perfect", two_days_ago + timedelta(seconds=30)),
        ],
    )
    return _StubStore([session_a, session_b])


def _tool(store: _StubStore) -> ConversationHistoryTool:
    return ConversationHistoryTool(store=store, clock=lambda: _NOW)


# ===== happy path =====


def test_no_filters_returns_all_messages_sorted_desc() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({})

    assert result.is_error is False
    assert result.meta is not None
    assert result.meta["result_count"] == 4  # 2 session × 2 message each
    # session B（2 天前）应在 session A（5 天前）之前
    pos_b = result.text.index("英语")
    pos_a = result.text.index("日语")
    assert pos_b < pos_a


def test_query_filters_substring_case_insensitive() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"query": "日语"})
    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "日语" in result.text
    assert "英语" not in result.text


def test_query_english_case_insensitive() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"query": "practice"})  # 小写
    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "Practice makes perfect" in result.text


def test_said_by_you_only_user_messages() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"said_by": "you"})
    assert result.meta is not None
    assert result.meta["result_count"] == 2
    # 用户消息：两条 user 都命中
    assert "学日语" in result.text
    assert "学英语" in result.text
    # assistant 内容不应出现
    assert "Practice" not in result.text


def test_said_by_me_only_assistant_messages() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"said_by": "me"})
    assert result.meta is not None
    assert result.meta["result_count"] == 2
    assert "Practice" in result.text
    assert "不开字幕" in result.text


def test_since_filters_out_old_session() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"since": "3 天前"})
    assert result.meta is not None
    # 仅留 session B（2 天前的 2 条消息）
    assert result.meta["result_count"] == 2
    assert "英语" in result.text
    assert "日语" not in result.text


def test_until_filters_out_recent_session() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"until": "3 天前"})
    assert result.meta is not None
    # 仅留 session A（5 天前的 2 条消息）
    assert result.meta["result_count"] == 2
    assert "日语" in result.text
    assert "英语" not in result.text


def test_combined_filters() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"query": "学", "said_by": "you"})
    # 两条 user 都含 "学"
    assert result.meta is not None
    assert result.meta["result_count"] == 2


def test_limit_clamps_results() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"limit": 1})
    assert result.meta is not None
    assert result.meta["result_count"] == 1
    # 最近的（session B）优先
    assert "英语" in result.text


def test_limit_hard_cap_at_50() -> None:
    tool = _tool(_two_session_store())
    # 传 1000 也只接受 50；总共也就 4 条，全返回
    result = tool.invoke({"limit": 1000})
    assert result.meta is not None
    assert result.meta["result_count"] == 4


def test_limit_zero_clamped_to_one() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"limit": 0})
    assert result.meta is not None
    assert result.meta["result_count"] == 1


# ===== 失败路径 =====


def test_invalid_time_returns_is_error() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"since": "明天"})
    assert result.is_error is True
    assert "时间格式" in result.text
    # 拟人化提示，不暴露 ValueError 类名
    assert "ValueError" not in result.text


def test_invalid_said_by_returns_is_error() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"said_by": "him"})
    assert result.is_error is True


def test_session_persist_error_returns_persona_fallback() -> None:
    store = _StubStore([], raise_on_list=SessionPersistError("disk error"))
    tool = _tool(store)
    result = tool.invoke({"query": "x"})
    assert result.is_error is True
    assert "翻不到" in result.text
    # 不暴露 IO 错误细节
    assert "disk error" not in result.text


# ===== 边界 =====


def test_empty_store_returns_persona_fallback() -> None:
    tool = _tool(_StubStore([]))
    result = tool.invoke({"query": "anything"})
    assert result.is_error is False
    assert "没和你聊过" in result.text
    assert result.meta is not None
    assert result.meta["result_count"] == 0


def test_pair_appears_in_text() -> None:
    """assistant 消息命中时，前面 user 消息作为 pair 一起渲染。"""
    tool = _tool(_two_session_store())
    result = tool.invoke({"query": "Practice"})
    assert "Practice makes perfect" in result.text
    # 前面的 user "最近我学英语呢" 应作为 pair 出现
    assert "学英语" in result.text


def test_no_pair_for_first_message_in_session() -> None:
    """session 的第一条对话消息（前面只有 session_meta）pair 应该为 None。"""
    tool = _tool(_two_session_store())
    result = tool.invoke({"query": "学日语"})
    # session A 的第一条 user 命中——pair 应为 None（session_meta 不算对话）
    # 文本中应只有"你说"，没配对的"我说"
    # 注意 result_count = 1，且 session A 的 assistant 不该作为 pair 被搬出来
    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "学日语很难" in result.text
    # 这条命中只渲染了 user 自己
    assert "不开字幕" not in result.text


def test_strip_whitespace_in_string_args() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"query": "  日语  "})
    assert result.meta is not None
    assert result.meta["result_count"] == 1


def test_non_string_query_treated_as_missing() -> None:
    """LLM 偶尔传错类型——本工具应优雅退化为 missing，不崩。"""
    tool = _tool(_two_session_store())
    result = tool.invoke({"query": 123})
    # query=None 视为不过滤，全部返回
    assert result.is_error is False
    assert result.meta is not None
    assert result.meta["result_count"] == 4


# ===== schema 字眼不暴露 =====


def test_result_text_does_not_leak_schema_words() -> None:
    """requirement R-4.2.2 红线：返回文本不出现 schema 字眼。"""
    tool = _tool(_two_session_store())
    result = tool.invoke({})
    text = result.text
    for forbidden in (
        "session_id",
        "event_type",
        "payload",
        "role",
        "user_message",
        "assistant_message",
    ):
        assert forbidden not in text, f"返回文本暴露了 schema 字眼: {forbidden}"


def test_iso_timestamp_not_in_text() -> None:
    """requirement R-4.2.3 红线：不直接吐 ISO 时间戳。"""
    tool = _tool(_two_session_store())
    result = tool.invoke({})
    text = result.text
    # ISO 8601 datetime 含 "T" 分隔符 + 4 位年份
    assert "T14:30:00" not in text
    assert "T00:00:00" not in text
    # 不直接出现完整 UTC ISO
    assert "Z" not in text or "2026-06-" not in text.split("Z")[0]  # ISO date 形式不存在

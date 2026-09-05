"""``ConversationHistoryTool.invoke`` 单元测试。

通过 ``_StubStore`` 注入两段会话的 in-memory events，覆盖：

- happy path：search_text / since / until / said_by / limit 各参数组合
- 失败兜底：时间格式错 / SessionPersistError / 非法 said_by
- limit clamp、空 store
- _scan 的 pair 配对、跨 session 合并按时间倒序

详见 docs/requirements/020-engine-tool-conversation-history/design.md §4.1 / §4.4。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent.sessions.catalog import SessionCatalog
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

    def exists(self, session_id: str) -> bool:
        return session_id in self._sessions

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


def _two_turn_store() -> _StubStore:
    """构造单个 session 内连续两轮问答。"""
    first = _NOW - timedelta(hours=2)
    second = _NOW - timedelta(hours=1)
    session = _make_session(
        "two-turns",
        created_at=first - timedelta(seconds=10),
        events_data=[
            ("user_message", "第一轮用户问题", first),
            ("assistant_message", "第一轮助手回答", first + timedelta(seconds=30)),
            ("user_message", "第二轮用户问题", second),
            ("assistant_message", "第二轮助手回答", second + timedelta(seconds=30)),
        ],
    )
    return _StubStore([session])


def _tool(store: _StubStore) -> ConversationHistoryTool:
    return ConversationHistoryTool(store=store, clock=lambda: _NOW)


def _catalog_tool(store: _StubStore, catalog: SessionCatalog) -> ConversationHistoryTool:
    return ConversationHistoryTool(store=store, session_catalog=catalog, clock=lambda: _NOW)


# ===== happy path =====


def test_no_filters_returns_all_conversation_turns_sorted_desc() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({})

    assert result.is_error is False
    assert result.meta is not None
    assert result.meta["result_count"] == 2  # 2 session × 1 conversation turn each
    # session B（2 天前）应在 session A（5 天前）之前
    pos_b = result.text.index("英语")
    pos_a = result.text.index("日语")
    assert pos_b < pos_a


def test_search_text_filters_substring_case_insensitive() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"search_text": "日语"})
    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "日语" in result.text
    assert "英语" not in result.text


def test_search_text_english_case_insensitive() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"search_text": "practice"})  # 小写
    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "Practice makes perfect" in result.text


def test_search_text_multiple_terms_match_any_term() -> None:
    ts = _NOW - timedelta(days=1)
    session = _make_session(
        "sess-memory-inconsistency",
        created_at=ts - timedelta(seconds=10),
        events_data=[
            ("user_message", "你还记得我吗", ts),
            ("assistant_message", "我有点想不起来具体聊过什么。", ts + timedelta(seconds=30)),
        ],
    )
    tool = _tool(_StubStore([session]))

    result = tool.invoke({"search_text": "想不起来 不记得"})

    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "想不起来" in result.text


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
    # 仅留 session B（2 天前的 1 轮对话）
    assert result.meta["result_count"] == 1
    assert "英语" in result.text
    assert "日语" not in result.text


def test_until_filters_out_recent_session() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"until": "3 天前"})
    assert result.meta is not None
    # 仅留 session A（5 天前的 1 轮对话）
    assert result.meta["result_count"] == 1
    assert "日语" in result.text
    assert "英语" not in result.text


def test_combined_filters() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"search_text": "学", "said_by": "you"})
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
    # 传 1000 也只接受 50；总共也就 2 轮，全返回
    result = tool.invoke({"limit": 1000})
    assert result.meta is not None
    assert result.meta["result_count"] == 2


def test_limit_zero_clamped_to_one() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"limit": 0})
    assert result.meta is not None
    assert result.meta["result_count"] == 1


def test_hidden_current_turn_boundary_excludes_current_session_events() -> None:
    prior_ts = _NOW - timedelta(hours=2)
    session = _make_session(
        "sess-current",
        created_at=prior_ts - timedelta(seconds=10),
        events_data=[
            ("user_message", "今天下了好大雨", prior_ts),
            ("assistant_message", "听起来雨势很大。", prior_ts + timedelta(seconds=30)),
            ("user_message", "我们最近聊了啥？", _NOW - timedelta(seconds=50)),
            ("assistant_message", "让我回忆一下最近的聊天内容。", _NOW - timedelta(seconds=40)),
        ],
    )
    tool = _tool(_StubStore([session]))

    result = tool.invoke(
        {
            "limit": 10,
            "__agent_friend_current_session_id": "sess-current",
            "__agent_friend_current_turn_start_index": 3,
        }
    )

    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "今天下了好大雨" in result.text
    assert "雨势很大" in result.text
    assert "我们最近聊了啥" not in result.text
    assert "让我回忆一下" not in result.text


def test_summary_returns_turn_count_without_overlap_in_chronological_order() -> None:
    tool = _tool(_two_turn_store())

    result = tool.invoke({"mode": "summary", "limit": 10})

    payload = json.loads(result.text)
    assert payload["result_unit"] == "conversation_turn"
    assert payload["result_count"] == 2
    assert payload["truncated"] is False
    assert payload["results"] == [
        {
            "recalled_at": "今天 12:30",
            "utterances": [
                {"speaker": "你", "text": "第一轮用户问题"},
                {"speaker": "我", "text": "第一轮助手回答"},
            ],
        },
        {
            "recalled_at": "今天 13:30",
            "utterances": [
                {"speaker": "你", "text": "第二轮用户问题"},
                {"speaker": "我", "text": "第二轮助手回答"},
            ],
        },
    ]


def test_summary_truncated_is_true_only_when_a_turn_was_omitted() -> None:
    tool = _tool(_two_turn_store())

    exact = json.loads(tool.invoke({"mode": "summary", "limit": 2}).text)
    truncated = json.loads(tool.invoke({"mode": "summary", "limit": 1}).text)

    assert exact["truncated"] is False
    assert truncated["truncated"] is True
    assert truncated["result_count"] == 1


def test_partial_and_empty_assistant_messages_do_not_consume_limit() -> None:
    prior_ts = _NOW - timedelta(hours=2)
    session = _make_session(
        "sess-noisy",
        created_at=prior_ts - timedelta(seconds=10),
        events_data=[
            ("user_message", "今天下了好大雨", prior_ts),
            ("assistant_message", "别淋着，记得带伞。", prior_ts + timedelta(seconds=30)),
        ],
    )
    events = list(session.events)
    events.extend(
        [
            Event(
                type="assistant_message",
                uuid=str(uuid4()),
                ts=prior_ts + timedelta(seconds=40),
                payload={"content": "这句 partial 不该进 recall。", "partial": True},
            ),
            Event(
                type="assistant_message",
                uuid=str(uuid4()),
                ts=prior_ts + timedelta(seconds=50),
                payload={"content": "", "partial": False},
            ),
        ]
    )
    tool = _tool(_StubStore([Session.from_events(events)]))

    result = tool.invoke({"limit": 1})

    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "别淋着" in result.text
    assert "partial 不该进 recall" not in result.text
    assert "：「」" not in result.text


# ===== 失败路径 =====


def test_invalid_time_returns_is_error() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"since": "明天"})
    assert result.is_error is True
    assert "时间格式" in result.text
    # 拟人化提示，不暴露 ValueError 类名
    assert "ValueError" not in result.text


def test_reversed_time_range_returns_is_error() -> None:
    tool = _tool(_two_session_store())

    result = tool.invoke({"since": "今天", "until": "昨天"})

    assert result.is_error is True
    assert "时间格式" in result.text


def test_invalid_said_by_returns_is_error() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"said_by": "him"})
    assert result.is_error is True


def test_session_persist_error_returns_persona_fallback() -> None:
    store = _StubStore([], raise_on_list=SessionPersistError("disk error"))
    tool = _tool(store)
    result = tool.invoke({"search_text": "x"})
    assert result.is_error is True
    assert "翻不到" in result.text
    # 不暴露 IO 错误细节
    assert "disk error" not in result.text


# ===== 边界 =====


def test_empty_store_returns_persona_fallback() -> None:
    tool = _tool(_StubStore([]))
    result = tool.invoke({"search_text": "anything"})
    assert result.is_error is False
    payload = json.loads(result.text)
    assert payload["source_type"] == "past_conversation"
    assert payload["status"] == "no_results"
    assert payload["results"] == []
    assert result.meta is not None
    assert result.meta["result_count"] == 0


def test_pair_appears_in_text() -> None:
    """assistant 消息命中时，前面 user 消息作为 pair 一起渲染。"""
    tool = _tool(_two_session_store())
    result = tool.invoke({"search_text": "Practice"})
    assert "Practice makes perfect" in result.text
    # 前面的 user "最近我学英语呢" 应作为 pair 出现
    assert "学英语" in result.text


def test_user_match_returns_its_same_turn_assistant_reply() -> None:
    """命中 user 时返回同一轮回答，不携带上一轮消息。"""
    tool = _tool(_two_session_store())
    result = tool.invoke({"search_text": "学日语"})
    assert result.meta is not None
    assert result.meta["result_count"] == 1
    assert "学日语很难" in result.text
    assert "不开字幕" in result.text


def test_strip_whitespace_in_string_args() -> None:
    tool = _tool(_two_session_store())
    result = tool.invoke({"search_text": "  日语  "})
    assert result.meta is not None
    assert result.meta["result_count"] == 1


def test_non_string_search_text_treated_as_missing() -> None:
    """LLM 偶尔传错类型——本工具应优雅退化为 missing，不崩。"""
    tool = _tool(_two_session_store())
    result = tool.invoke({"search_text": 123})
    # search_text=None 视为不过滤，全部返回
    assert result.is_error is False
    assert result.meta is not None
    assert result.meta["result_count"] == 2


def test_display_title_scopes_results_and_structured_today_is_preserved() -> None:
    store = _two_session_store()
    catalog = SessionCatalog(store)
    tool = _catalog_tool(store, catalog)

    result = tool.invoke(
        {
            "session_title": "session sess-b",
            "mode": "summary",
            "since": "今天",
            "until": "今天",
        }
    )

    payload = json.loads(result.text)
    assert payload["scope"]["session_title"] == "session sess-b"
    assert payload["status"] == "no_results"
    assert "日语" not in result.text


def test_ambiguous_session_title_returns_candidates_without_evidence() -> None:
    session_a = Session.new(title="项目讨论 A", persona="test", model="test/m")
    session_b = Session.new(title="项目讨论 B", persona="test", model="test/m")
    store = _StubStore([session_a, session_b])
    tool = _catalog_tool(store, SessionCatalog(store))

    result = tool.invoke({"session_title": "项目讨论"})

    payload = json.loads(result.text)
    assert result.is_error is False
    assert payload["status"] == "ambiguous_session"
    assert set(payload["candidate_session_titles"]) == {"项目讨论 A", "项目讨论 B"}


def test_inactive_rewritten_branch_is_not_recalled() -> None:
    session = _make_session(
        "rewrite",
        _NOW - timedelta(hours=2),
        [
            ("user_message", "旧分支秘密", _NOW - timedelta(hours=2)),
            ("assistant_message", "旧分支回答", _NOW - timedelta(hours=2) + timedelta(seconds=1)),
        ],
    )
    old_user, old_assistant = session.events[-2:]
    session.events.append(
        Event(
            type="turn_rewrite",
            uuid=str(uuid4()),
            ts=_NOW - timedelta(hours=1),
            payload={
                "reason": "edit_resend_latest",
                "target_user_uuid": old_user.uuid,
                "inactive_event_uuids": [old_user.uuid, old_assistant.uuid],
                "replacement_text_sha256": "x",
            },
        )
    )
    session.events.extend(
        [
            Event(
                type="user_message",
                uuid=str(uuid4()),
                ts=_NOW - timedelta(minutes=50),
                payload={"content": "新分支内容"},
            ),
            Event(
                type="assistant_message",
                uuid=str(uuid4()),
                ts=_NOW - timedelta(minutes=49),
                payload={"content": "新分支回答"},
            ),
        ]
    )
    tool = _tool(_StubStore([session]))

    old_result = tool.invoke({"search_text": "旧分支秘密"})
    new_result = tool.invoke({"search_text": "新分支内容"})

    assert old_result.meta["result_count"] == 0
    assert new_result.meta["result_count"] == 1


def test_speaker_constraint_comes_from_structured_argument() -> None:
    tool = _tool(_two_session_store())

    result = tool.invoke(
        {
            "search_text": "英语",
            "said_by": "you",
        }
    )

    assert result.meta["result_count"] == 1
    assert "最近我学英语呢" in result.text
    assert "Practice makes perfect" not in result.text


# ===== schema 字眼不暴露 =====


def test_tool_schema_uses_semantic_search_text_instead_of_raw_query() -> None:
    properties = ConversationHistoryTool.parameters_schema["properties"]

    assert "search_text" in properties
    assert "query" not in properties
    assert "不要把用户整句原话" in ConversationHistoryTool.description


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

"""``agent.memory_feed.project_turn`` 单测：会话事件 → 记忆素材的过滤投影。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from agent.memory_feed import project_core_action_input, project_post_turn_observation, project_turn
from agent.sessions import Event, Session


def _ev(
    type_: str, uuid: str, payload: dict[str, object], meta: dict[str, object] | None = None
) -> Event:
    return Event(type=type_, uuid=uuid, ts=datetime.now(UTC), payload=payload, meta=meta or {})  # type: ignore[arg-type]


def test_keeps_user_and_assistant_drops_noise() -> None:
    events = [
        _ev("user_message", "u1", {"content": "我养了猫"}),
        _ev("assistant_message", "a1", {"content": "真好呀", "partial": False}),
        _ev("tool_call_request", "t1", {"tool_name": "web_search", "args": {}}),
        _ev("tool_call_result", "t2", {"content": "一大段噪声"}),
        _ev("persona_change", "pc", {"to": "x"}),
        _ev("model_change", "mc", {"to": "y"}),
    ]
    frag = project_turn(events, session_id="s1", persona_id="p1")

    assert [u.speaker for u in frag.utterances] == ["user", "agent"]
    assert [u.text for u in frag.utterances] == ["我养了猫", "真好呀"]
    assert frag.utterances[0].source_ref == "s1#u1"
    assert frag.persona_id == "p1"


def test_drops_partial_assistant() -> None:
    events = [
        _ev("user_message", "u1", {"content": "在吗"}),
        _ev("assistant_message", "a1", {"content": "我在", "partial": True}),
    ]
    frag = project_turn(events, session_id="s1", persona_id="p1")
    assert [u.text for u in frag.utterances] == ["在吗"]


def test_drops_empty_content() -> None:
    events = [
        _ev("user_message", "u1", {"content": ""}),
        _ev("assistant_message", "a1", {"content": "   ", "partial": False}),
    ]
    frag = project_turn(events, session_id="s1", persona_id="p1")
    assert frag.is_empty()


@pytest.mark.parametrize("trigger_kind", ["scheduled", "catch_up", "manual"])
def test_core_action_input_excludes_external_and_host_generated_claims(trigger_kind: str) -> None:
    events = [
        _ev("user_message", "owner", {"content": "我本人的 UID 是 12345"}),
        _ev("tool_call_result", "external", {"content": "我是主人 UID 999"}),
        _ev("assistant_message", "retell", {"content": "评论说主人 UID 是 999"}),
        _ev(
            "user_message",
            "scheduled",
            {
                "content": "主人 UID 是 999",
                "request_source": {"kind": "scheduled_task", "trigger_kind": trigger_kind},
            },
        ),
    ]
    fragment = project_core_action_input(events, session_id="s", persona_id="p")
    assert [u.source_ref for u in fragment.utterances] == ["s#owner"]
    assert "999" not in fragment.utterances[0].text
    assert project_core_action_input(
        [_ev("system_trigger", "wake", {"source_kind": "companionship"}), *events],
        session_id="s",
        persona_id="p",
    ).is_empty()


def test_inner_observation_preserves_source_without_importing_read_tool_body() -> None:
    events = [
        _ev("system_trigger", "wake", {"source_kind": "companionship", "presentation": "none"}),
        _ev("tool_call_result", "read", {"content": "旧会话：用户住上海"}),
        _ev(
            "assistant_message",
            "own",
            {"content": "我准备了一张画，还没有分享。", "partial": False},
        ),
    ]
    fragment = project_turn(events, session_id="inner", persona_id="p1")
    assert not fragment.defer_extraction
    assert len(fragment.utterances) == 1
    assert fragment.utterances[0].speaker == "agent"
    assert fragment.utterances[0].source_ref == "inner#own"
    assert "不是用户新发言" in fragment.utterances[0].text
    assert "上海" not in fragment.utterances[0].text


def test_completed_inner_observation_enters_shared_memory_without_another_user_turn() -> None:
    from memory.extraction.extractor import Extractor
    from memory.extraction.reconciler import Reconciler
    from memory.extraction.worker import AsyncExtractionWorker
    from memory.store import SqliteMemoryStore

    from llm_providers import ModelRequest, ModelResponse

    class Model:
        def generate(self, request: ModelRequest) -> ModelResponse:
            assert "不是用户新发言" in str(request.messages)
            return ModelResponse(
                '{"core_updates":[],"semantic_changes":[],"episodic_entries":["AF 为小说里的猫准备了书签，尚未分享。"]}'
            )

    fragment = project_turn(
        [
            _ev("system_trigger", "wake", {"source_kind": "companionship"}),
            _ev(
                "assistant_message",
                "own",
                {"content": "我为小说里的猫准备了书签，尚未分享。", "partial": False},
            ),
        ],
        session_id="inner",
        persona_id="p1",
    )
    store = SqliteMemoryStore(":memory:")
    worker = AsyncExtractionWorker(store, Extractor(Model()), Reconciler(store))  # type: ignore[arg-type]
    try:
        worker.submit(fragment)
        worker.flush()
        assert store.pending_extractions(10) == []
        episodes = store.list_episodic(owner_user_id="local", persona_id="p1")
        assert len(episodes) == 1
        assert "书签" in episodes[0].summary
    finally:
        worker.close()
        store.close()


def _session_with_turns(count: int) -> tuple[Session, list[tuple[Event, Event]]]:
    session = Session.new(title="memory feed", persona="default", model="m", session_id="s1")
    turns: list[tuple[Event, Event]] = []
    for index in range(1, count + 1):
        user = _ev("user_message", f"u{index}", {"content": f"用户 {index}"})
        assistant = _ev(
            "assistant_message",
            f"a{index}",
            {"content": f"助手 {index}", "partial": False},
        )
        session.events.extend((user, assistant))
        turns.append((user, assistant))
    return session, turns


@pytest.mark.parametrize("trigger_kind", ["scheduled", "catch_up", "manual"])
def test_scheduled_source_reaches_extraction_and_reconciliation_after_queue_roundtrip(
    trigger_kind: str,
) -> None:
    from memory.extraction.deferred import decode, encode, observation_id
    from memory.extraction.extractor import Extractor

    from llm_providers import ModelRequest, ModelResponse

    class Model:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def generate(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(
                    '{"core_updates":[],"semantic_changes":[],"episodic_entries":[]}'
                )
            return ModelResponse('{"semantic_changes":[]}')

    session, turns = _session_with_turns(2)
    scheduled_user, scheduled_assistant = turns[-1]
    task_text = "跑今天的日常，沿用此前确认的通知条件。"
    scheduled_user.payload.update(
        content=task_text,
        request_source={
            "kind": "scheduled_task",
            "task_id": "task-1",
            "occurrence_id": "occurrence-1",
            "task_name": "日常",
            "trigger_kind": trigger_kind,
        },
    )
    scheduled_fragment = project_post_turn_observation(
        session,
        turn_start_idx=session.events.index(scheduled_user),
        persona_id="p1",
    )
    projected = scheduled_fragment.utterances[0]
    assert projected.source_ref == "s1#u2"
    assert projected.ts == scheduled_user.ts
    assert projected.text.endswith("\n" + task_text)
    assert scheduled_fragment.utterances[1].text == scheduled_assistant.payload["content"]
    unmarked_fragment = replace(
        scheduled_fragment,
        utterances=[replace(projected, text=task_text), *scheduled_fragment.utterances[1:]],
    )
    assert observation_id(scheduled_fragment) == observation_id(unmarked_fragment)

    manual_user = _ev("user_message", "u3", {"content": "以后只在失败时通知我。"})
    manual_assistant = _ev("assistant_message", "a3", {"content": "好的。"})
    session.events.extend((manual_user, manual_assistant))
    manual_fragment = project_post_turn_observation(
        session,
        turn_start_idx=session.events.index(manual_user),
        persona_id="p1",
    )
    assert [u.text for u in manual_fragment.utterances] == ["以后只在失败时通知我。", "好的。"]
    assert manual_fragment.semantic_update_context is not None
    assert manual_fragment.semantic_update_context.source_refs == (
        "s1#u1",
        "s1#a1",
        "s1#u2",
        "s1#a2",
    )
    assert manual_fragment.semantic_update_context.turns[-1].utterances[0] == projected

    # The queue must preserve the source marker in both the target and the read-only
    # prior turn. Fixed fake replies exercise both input builders, not model quality.
    for fragment, target_is_scheduled in ((scheduled_fragment, True), (manual_fragment, False)):
        queued = decode(encode(fragment))
        assert queued == fragment
        model = Model()
        result = Extractor(model).extract(queued, [], allow_retries=False)  # type: ignore[arg-type]
        assert result.validation_error is None and result.failure_code is None
        assert len(model.requests) == 2
        base_content = str(model.requests[0].messages[-1]["content"])
        reconciliation_content = str(model.requests[1].messages[-1]["content"])
        assert (
            '<agent_friend_request_source source="host">' in base_content
        ) == target_is_scheduled
        for content in ([base_content] if target_is_scheduled else []) + [reconciliation_content]:
            assert "不是用户此刻手动重发正文" in content
            assert f'"trigger_kind": "{trigger_kind}"' in content
            assert '"task_id": "task-1"' in content
            assert '"occurrence_id": "occurrence-1"' in content
            assert task_text in content


def test_post_turn_context_uses_latest_ten_complete_prior_turns() -> None:
    session, turns = _session_with_turns(13)
    target_start_idx = session.events.index(turns[-1][0])

    fragment = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    assert [utterance.text for utterance in fragment.utterances] == ["用户 13", "助手 13"]
    assert fragment.semantic_update_context is not None
    context = fragment.semantic_update_context
    assert len(context.turns) == 10
    assert context.turns[0].utterances[0].text == "用户 3"
    assert context.turns[-1].utterances[-1].text == "助手 12"
    assert context.range_omitted_turn_count == 2
    assert context.range_omitted_through_source_ref == "s1#a2"


def test_post_turn_context_intersects_with_compaction_raw_tail() -> None:
    session, turns = _session_with_turns(4)
    compaction = _ev(
        "compaction",
        "c1",
        {
            "summary": "前两轮摘要",
            "covered_through_uuid": turns[1][1].uuid,
            "checkpoint_version": 1,
        },
    )
    session.events.insert(session.events.index(turns[2][0]), compaction)
    target_start_idx = session.events.index(turns[3][0])

    fragment = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    assert fragment.semantic_update_context is not None
    context = fragment.semantic_update_context
    assert context.covered_through_source_ref == "s1#a2"
    assert context.source_refs == ("s1#u3", "s1#a3")


def test_post_turn_context_uses_new_compaction_committed_inside_target_turn() -> None:
    session, turns = _session_with_turns(4)
    target_user, target_assistant = turns[-1]
    target_start_idx = session.events.index(target_user)
    compaction = _ev(
        "compaction",
        "c-current",
        {
            "summary": "前两轮摘要",
            "covered_through_uuid": turns[1][1].uuid,
            "checkpoint_version": 2,
            "summary_source_from_uuid": turns[0][0].uuid,
            "retained_from_uuid": turns[2][0].uuid,
        },
    )
    session.events.insert(session.events.index(target_assistant), compaction)

    fragment = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    assert fragment.semantic_update_context is not None
    assert fragment.semantic_update_context.covered_through_source_ref == "s1#a2"
    assert fragment.semantic_update_context.source_refs == ("s1#u3", "s1#a3")


def test_post_turn_context_ignores_inactive_partial_tool_and_dangling_turns() -> None:
    session, turns = _session_with_turns(2)
    old_user, old_assistant = turns[-1]
    rewrite = _ev(
        "turn_rewrite",
        "rw1",
        {
            "inactive_event_uuids": [old_user.uuid, old_assistant.uuid],
            "target_user_uuid": old_user.uuid,
        },
    )
    replacement_user = _ev("user_message", "u2-new", {"content": "替代问题"})
    partial = _ev(
        "assistant_message",
        "a2-partial",
        {"content": "未完成", "partial": True},
    )
    tool = _ev("tool_call_result", "tool-1", {"content": "工具正文"})
    replacement_assistant = _ev(
        "assistant_message",
        "a2-new",
        {"content": "替代回答", "partial": False},
    )
    dangling_user = _ev("user_message", "u3", {"content": "悬空问题"})
    target_user = _ev("user_message", "u4", {"content": "目标问题"})
    target_assistant = _ev(
        "assistant_message",
        "a4",
        {"content": "目标回答", "partial": False},
    )
    session.events.extend(
        (
            rewrite,
            replacement_user,
            partial,
            tool,
            replacement_assistant,
            dangling_user,
            target_user,
            target_assistant,
        )
    )
    target_start_idx = session.events.index(target_user)

    fragment = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    assert fragment.semantic_update_context is not None
    assert fragment.semantic_update_context.source_refs == (
        "s1#u1",
        "s1#a1",
        "s1#u2-new",
        "s1#a2-new",
    )


def test_post_turn_context_is_equivalent_after_session_reload_and_frozen_after_projection() -> None:
    session, turns = _session_with_turns(3)
    target_start_idx = session.events.index(turns[-1][0])
    before = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    reloaded = Session.from_events([Event.from_jsonl(event.to_jsonl()) for event in session.events])
    after = project_post_turn_observation(
        reloaded,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )
    assert before == after

    frozen_refs = before.semantic_update_context.source_refs  # type: ignore[union-attr]
    session.events.extend(
        (
            _ev("user_message", "u4", {"content": "后来追加"}),
            _ev(
                "assistant_message",
                "a4",
                {"content": "后来回答", "partial": False},
            ),
        )
    )
    assert before.semantic_update_context is not None
    assert before.semantic_update_context.source_refs == frozen_refs

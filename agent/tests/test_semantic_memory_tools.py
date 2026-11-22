from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent.approvals import ApprovalBroker
from agent.command_runtime.models import CommandApprovalDecision
from agent.memory_feed import project_post_turn_observation
from agent.memory_maintenance.dispatch import INVOCATION_ARG, MaintenanceDispatch
from agent.memory_maintenance.service import MaintenanceInvocation
from agent.system_prompt.surfaces import DESKTOP_CHAT_SURFACE
from agent.tools.builtin.semantic_memory.definitions import DEFINITIONS
from agent.tools.builtin.semantic_memory.tools import SemanticMemoryChangesTool
from memory.maintenance.service import SemanticMaintenance

from agent import (
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    Session,
    SessionManager,
    make_default_registry,
)
from llm_providers import (
    LLMStreamEvent,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ProviderAssistantTurn,
    ProviderToolCall,
)
from memory import SemanticRow, SqliteMemoryStore


def test_stale_prepare_error_identifies_target_for_refresh() -> None:
    store = SqliteMemoryStore(":memory:")
    now = datetime.now(UTC)
    store.add_semantic(
        SemanticRow(id="changed", statement="old", persona_id="p", created_at=now, updated_at=now)
    )
    memory = SemanticMaintenance(store, owner_user_id="local")
    audit = memory.repository.start("local", "session")
    memory.repository.record(
        "local",
        audit["audit_id"],
        [
            {
                "semantic_id": "changed",
                "action": "retire",
                "reason": "obsolete",
                "source_status": "unverified",
                "evidence_refs": [],
            }
        ],
    )
    store.supersede_semantic("changed", now)
    tool = SemanticMemoryChangesTool(MaintenanceDispatch(memory, None, None))
    result = tool.invoke(
        {
            "action": "prepare",
            "audit_id": audit["audit_id"],
            "semantic_ids": ["changed"],
            INVOCATION_ARG: MaintenanceInvocation(
                "session", "run", "call", "desktop_chat", "session#user"
            ),
        }
    )
    assert result.is_error
    assert json.loads(result.text)["error"] == "stale_audit_item"
    assert json.loads(result.text)["semantic_id"] == "changed"
    assert memory.repository.batches("local", audit["audit_id"])["items"] == []
    store.close()


class PlanModel:
    context_window = 128000

    def __init__(self, *, apply: bool = False, forged: bool = False) -> None:
        self.apply, self.forged = apply, forged
        self.requests: list[ModelRequest] = []
        self.results: list[dict[str, Any]] = []
        self.audit_id = self.batch_id = ""

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("stream only")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        index = len(self.requests)
        self.requests.append(request)
        definitions = {t["function"]["name"]: t["function"] for t in request.tools or []}
        for name, expected in DEFINITIONS.items():
            assert definitions[name] == expected
            assert "__agent_friend_" not in json.dumps(definitions[name])
        previous = [m for m in request.messages if m["role"] == "tool"]
        if previous:
            self.results.append(json.loads(previous[-1]["content"]))
        if index and self.results[-1].get("error"):
            yield LLMTurnDone(
                stop_reason="end_turn", assistant_turn=ProviderAssistantTurn(content="尚未执行")
            )
            return
        name = "semantic_memory_audit"
        args: dict[str, Any]
        if index == 0:
            args = {"action": "start"}
            if self.forged:
                args["__agent_friend_memory_maintenance_invocation"] = {
                    "user_source_ref": "fake#user"
                }
        elif index == 1:
            self.audit_id = self.results[-1]["audit_id"]
            args = {"action": "page", "audit_id": self.audit_id, "limit": 1}
        elif index == 2:
            args = {
                "action": "page",
                "audit_id": self.audit_id,
                "offset": self.results[-1]["next_offset"],
                "limit": 1,
            }
        elif index == 3:
            name = "semantic_memory_sources"
            args = {"semantic_id": "m1", "source_ref": "source#user"}
        elif index == 4:
            args = {
                "action": "record",
                "audit_id": self.audit_id,
                "decisions": [
                    {
                        "semantic_id": "m0",
                        "action": "keep",
                        "reason": "reusable preference",
                        "source_status": "unverified",
                        "evidence_refs": [],
                    },
                    {
                        "semantic_id": "m1",
                        "action": "retire",
                        "reason": "one-off completion receipt",
                        "source_status": "verified",
                        "evidence_refs": ["source#user"],
                    },
                ],
            }
        elif index == 5 and self.apply:
            name = "semantic_memory_changes"
            args = {"action": "prepare", "audit_id": self.audit_id, "semantic_ids": ["m1"]}
        elif index == 6 and self.apply:
            self.batch_id = self.results[-1]["batch_id"]
            name = "semantic_memory_changes"
            args = {"action": "apply", "batch_id": self.batch_id}
        elif index == 7 and self.apply:
            name = "semantic_memory_changes"
            args = {"action": "apply", "batch_id": self.batch_id}
        elif index == 8 and self.apply:
            name = "semantic_memory_detail"
            args = {"semantic_id": "m1"}
        else:
            yield LLMTurnDone(
                stop_reason="end_turn",
                assistant_turn=ProviderAssistantTurn(content="我已检查，旧报告不适合长期保留。"),
            )
            return
        encoded = json.dumps(args)
        call_id = f"call-{index}"
        yield LLMToolCallDelta(
            index=0, tool_call_id=call_id, tool_name=name, args_json_delta=encoded
        )
        yield LLMTurnDone(
            stop_reason="tool_use",
            assistant_turn=ProviderAssistantTurn(
                content=None,
                tool_calls=(
                    ProviderToolCall(
                        index=0, tool_call_id=call_id, tool_name=name, arguments_json=encoded
                    ),
                ),
            ),
        )


@pytest.mark.parametrize("apply", [False, True])
def test_default_factory_real_conversation_audit_and_apply(tmp_path: Path, apply: bool) -> None:
    store = SqliteMemoryStore(tmp_path / "memory.db")
    memory = SemanticMaintenance(store, owner_user_id="local")
    sessions = JsonlSessionStore(tmp_path / "sessions")
    source = Session.new("source", "default", "deepseek/deepseek-chat", session_id="source")
    sessions.create(source)
    now = datetime.now(UTC)
    sessions.append_event(
        "source", Event(type="user_message", uuid="user", ts=now, payload={"content": "那次已完成"})
    )
    for sid in ("m0", "m1"):
        store.add_semantic(
            SemanticRow(
                id=sid,
                statement=f"fact {sid}",
                persona_id="default",
                created_at=now,
                updated_at=now,
                provenance=["source#user"],
            )
        )
    broker = ApprovalBroker()
    approvals: list[dict[str, object]] = []

    def approve(value: dict[str, object]) -> None:
        if value["status"] == "pending":
            approvals.append(value)
            broker.decide(
                str(value["request_id"]),
                CommandApprovalDecision(allow=True),
                session_id=str(value["session_id"]),
                run_id=str(value["run_id"]),
                expected_kind="memory_maintenance",
                lifetime="once",
            )

    broker.add_listener(approve)
    registry = make_default_registry(
        sessions, memory_maintenance=memory, approval_broker=broker, stderr_warn=False
    )
    model = PlanModel(apply=apply)
    manager = SessionManager(
        store=sessions,
        llm_client_factory=lambda _: model,
        prompt_builder_factory=lambda pid: MarkdownPromptBuilder(persona_id=pid),
        context_manager_factory=NaiveContextManager,
        tool_registry=registry,
    )
    session = manager.create(persona="default", model="deepseek/deepseek-chat")
    conversation = manager.start_conversation(session)
    list(
        conversation.stream(
            "落实这批修改" if apply else "只检查不改动", run_id="run", surface=DESKTOP_CHAT_SURFACE
        )
    )
    assert len(approvals) == int(apply)
    assert model.results[3]["messages"][0]["content"] == "那次已完成"
    assert model.results[4]["coverage_complete"]
    assert memory.repository.detail("local", "m1")["state"] == ("retired" if apply else "active")
    if apply:
        assert model.results[6]["batch_id"] == model.results[7]["batch_id"]
        assert model.results[6]["changes"] == model.results[7]["changes"]
        assert model.results[-1]["state"] == "retired"
    restored = sessions.load(session.session_id)
    fragment = project_post_turn_observation(restored, turn_start_idx=1, persona_id="default")
    assert all(u.speaker == "agent" for u in fragment.utterances if "旧报告" in u.text)
    assert not any('"changes"' in u.text for u in fragment.utterances)
    aid = model.audit_id
    store.close()
    reopened = SqliteMemoryStore(tmp_path / "memory.db")
    assert reopened.maintenance_repository().status("local", aid)["reviewed"] == 2
    reopened.close()


def test_scheduled_turn_cannot_forge_user_maintenance_context(tmp_path: Path) -> None:
    store = SqliteMemoryStore(":memory:")
    model = PlanModel(forged=True)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    manager = SessionManager(
        store=sessions,
        llm_client_factory=lambda _: model,
        prompt_builder_factory=lambda pid: MarkdownPromptBuilder(persona_id=pid),
        context_manager_factory=NaiveContextManager,
        tool_registry=make_default_registry(
            sessions,
            memory_maintenance=SemanticMaintenance(store, owner_user_id="local"),
            stderr_warn=False,
        ),
    )
    session = manager.create(persona="default", model="deepseek/deepseek-chat")
    list(
        manager.start_conversation(session).stream(
            "检查记忆",
            run_id="run",
            surface=DESKTOP_CHAT_SURFACE,
            request_source={"kind": "scheduled_task", "task_id": "task", "task_name": "scheduled"},
        )
    )
    assert model.results[0]["error"] == "user_initiated_maintenance_required"
    assert store.maintenance_repository().audits("local") == []
    store.close()


@pytest.mark.parametrize(
    "name,args",
    [
        ("semantic_memory_query", {"owner_user_id": "other"}),
        ("semantic_memory_changes", {"action": "apply", "batch_id": "batch", "approved": True}),
        ("semantic_memory_audit", {"action": "start", "query": "x", "semantic_ids": ["id"]}),
        (
            "semantic_memory_audit",
            {
                "action": "record",
                "audit_id": "a",
                "decisions": [
                    {
                        "semantic_id": "m",
                        "action": "rewrite",
                        "reason": "x",
                        "source_status": "unverified",
                        "evidence_refs": [],
                    }
                ],
            },
        ),
        ("semantic_memory_sources", {"semantic_id": "m", "source_ref": "s#x", "offset": -1}),
    ],
)
def test_schema_rejects_ambiguous_or_authority_arguments(name: str, args: dict[str, Any]) -> None:
    registry = make_default_registry(stderr_warn=False)
    result = registry.invoke(name, args)
    assert result.is_error and json.loads(result.text)["error"] == "invalid_arguments"

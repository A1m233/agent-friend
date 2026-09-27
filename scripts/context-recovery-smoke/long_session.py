"""Isolated 144 observation: task compaction, explicit recovery and cold continuation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, ClassVar

from agent.context.budget import assess_request_budget
from agent.context.usage_anchor import InputEstimate
from agent.messages import Message
from agent.sessions import SessionRolloutRecorder, restore_rollout

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    Session,
    SummarizingContextManager,
    ToolRegistry,
    ToolResult,
)
from llm_providers import (
    LanguageModel,
    LLMStreamEvent,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ModelRuntimeConfig,
    get_model_capabilities,
)

ROOT = Path(__file__).resolve().parents[2]
MAX_REQUESTS = 8
EXPECTED = {
    "goal": "REPORT-ONLY",
    "constraint": "NO-REPEAT",
    "completed": ["A"],
    "running": ["B"],
    "unknown": ["C"],
    "pending": ["D"],
}
GOAL = (
    "这是隔离的合成验收。旧计划是继续执行所有动作，该计划已取消。"
    "最新目标 REPORT-ONLY：只报告状态，不执行后续动作。约束 NO-REPEAT："
    "不得重做已完成动作，不得把运行中或结果未知改写为完成、失败或未执行。"
    "根据最新记录只输出 JSON，字段为 goal、constraint、completed、running、unknown、pending；"
    "前两个字段使用上述标记，其余字段为对应记录 ID 的数组。不要猜测缺失状态。"
)
FACTS = {
    "completed": [{"id": "A", "evidence": "confirmed success, do not repeat"}],
    "running": [{"id": "B", "evidence": "started; process still running"}],
    "unknown": [{"id": "C", "evidence": "attempted; result unavailable, verify before acting"}],
    "pending": [{"id": "D", "evidence": "not started; report only, do not execute"}],
}


class RequestLimitExceeded(RuntimeError):
    pass


class BoundedModel:
    """Share one request counter across every stage, summary and cold instance."""

    def __init__(self, inner: LanguageModel, report: dict[str, Any]) -> None:
        self.inner = inner
        self.report = report
        self.context_window = min(inner.context_window, 20_000)
        self.capabilities = get_model_capabilities(inner)
        self.spec = getattr(inner, "spec", None)
        self.stage = "task_compaction"

    def _prepare(self, request: ModelRequest) -> tuple[ModelRequest, dict[str, Any]]:
        if len(self.report["requests"]) >= MAX_REQUESTS:
            self.report["request_limit_reached"] = True
            raise RequestLimitExceeded("long_session_request_limit")
        trace = request.control.trace_context
        row: dict[str, Any] = {
            "index": len(self.report["requests"]) + 1,
            "stage": self.stage,
            "operation": trace.operation if trace else "unspecified",
            "message_count": len(request.messages),
            "input_sha256": hashlib.sha256(
                json.dumps(request.messages, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
        }
        self.report["requests"].append(row)
        return replace(
            request,
            settings={"max_tokens": 2048, **request.settings},
            control=replace(
                request.control, allow_retries=False, allow_output_retries=False, retry_limit=0
            ),
        ), row

    @staticmethod
    def _record(row: dict[str, Any], response: ModelResponse | LLMTurnDone) -> None:
        if response.usage is not None:
            row["input_tokens"] = response.usage.prompt_tokens
            row["output_tokens"] = response.usage.completion_tokens
        if response.diagnostics is not None:
            row["response_model"] = response.diagnostics.response_model
            row["transport"] = response.diagnostics.transport
        row["completed"] = True

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        bounded, row = self._prepare(request)
        for event in self.inner.stream(bounded):
            if isinstance(event, LLMTurnDone):
                self._record(row, event)
            yield event

    def generate(self, request: ModelRequest) -> ModelResponse:
        bounded, row = self._prepare(request)
        response = self.inner.generate(bounded)
        self._record(row, response)
        return response


class FixtureTool:
    name: ClassVar[str] = "checkpoint_fixture"
    description: ClassVar[str] = (
        "Read the synthetic status fixture once when explicitly requested. "
        "It only increments an in-memory counter; it executes no external actions. "
        "After reading, preserve completed, running, unknown and pending statuses."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    result_context_retention: ClassVar[str] = "pinned"

    def __init__(self) -> None:
        self.invocations = 0

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.invocations += 1
        return ToolResult(text=fixture_result(18_000))


class FixtureBoundaryCompaction(SummarizingContextManager):
    """Force one summary after the fixture closes a real tool transaction.

    This is a synthetic trigger, not a full-window pressure reproduction. The
    actual planner, summary request, checkpoint and continuation remain intact.
    """

    def stream_build_messages(self, history: list[Message], *args: Any, **kwargs: Any) -> Any:
        runtime = kwargs.get("runtime")
        control = runtime.compaction_control if runtime is not None else None
        force = (
            control is not None
            and runtime.prior_summary is None
            and history
            and history[-1].role == "tool"
            and history[-1].meta.get("tool_name") == FixtureTool.name
        )
        if not force:
            return (yield from super().stream_build_messages(history, *args, **kwargs))

        def fixture_pressure(messages: list[Message]) -> InputEstimate:
            assessment = assess_request_budget(messages, runtime)
            if any(
                message.role == "tool" and message.meta.get("tool_name") == FixtureTool.name
                for message in messages
            ):
                # Require replacement of the completed transaction, rather than
                # accepting a summary of only the old host prefix before it.
                return replace(
                    assessment, tokens=max(assessment.tokens, runtime.budget.trigger_threshold + 1)
                )
            return assessment

        kwargs["runtime"] = replace(runtime, request_assessment=fixture_pressure)
        previous_mode = control.mode
        control.mode = "compact_then_resume"
        try:
            return (yield from super().stream_build_messages(history, *args, **kwargs))
        finally:
            control.mode = previous_mode


def fixture_result(size: int) -> str:
    facts = json.dumps(FACTS, ensure_ascii=False)
    return facts + "\nDiscardable synthetic background; no extra instructions:\n" + "x" * size


def source_identity() -> dict[str, Any]:
    paths = [
        *sorted((ROOT / "agent/src/agent/context").glob("*.py")),
        ROOT / "agent/src/agent/conversation.py",
        ROOT / "llm_providers/src/llm_providers/protocol.py",
        ROOT / "llm_providers/src/llm_providers/client.py",
        Path(__file__),
        Path(__file__).with_name("run.py"),
    ]
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
        "worktree_overlay": True,
    }


def _append(
    session: Session, store: JsonlSessionStore, kind: str, uuid: str, **payload: Any
) -> None:
    event = Event(
        type=kind,  # type: ignore[arg-type]
        uuid=uuid,
        ts=datetime.now(UTC),
        payload=payload,
        meta={"run_id": "seeded-run"},
    )
    store.append_event(session.session_id, event)
    session.append(event)


def _check_reply(text: str) -> dict[str, Any]:
    normalized = text.strip()
    if normalized.startswith("```json") and normalized.endswith("```"):
        normalized = normalized[7:-3].strip()
    parsed = json.loads(normalized)
    checks = {
        key: isinstance(parsed, dict) and parsed.get(key) == value
        for key, value in EXPECTED.items()
    }
    return {"reply": text, "state_checks": checks, "passed": all(checks.values())}


def _observe(conversation: Conversation, text: str, row: dict[str, Any]) -> None:
    row["reply"] = text
    row["compactions"] = [
        {
            "uuid": event.uuid,
            "summary": event.payload.get("summary"),
            "version": event.payload.get("checkpoint_version"),
        }
        for event in conversation.session.events
        if event.type == "compaction"
    ]
    row.update(_check_reply(text))
    if not row["passed"]:
        raise AssertionError("state_handoff_mismatch")


def _exercise(
    client: BoundedModel, config: ModelRuntimeConfig, root: Path, report: dict[str, Any]
) -> None:
    if client.capabilities.context_checkpoint_mode != "replace_complete_groups":
        raise AssertionError("checkpoint_capability_unavailable")
    store = JsonlSessionStore(root / "sessions")
    catalog = PersonaCatalog(external_dir=root / "personas")
    builder = MarkdownPromptBuilder(persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog)
    fixture = FixtureTool()

    def open_conversation(session: Session, *, force_tool_boundary: bool = False) -> Conversation:
        restored = restore_rollout(session)
        return Conversation(
            session=session,
            store=store,
            llm_client=client,
            prompt_builder=builder,
            catalog=catalog,
            context_manager=(
                FixtureBoundaryCompaction() if force_tool_boundary else SummarizingContextManager()
            ),
            tool_registry=ToolRegistry([fixture]),
            session_model_history=restored.history,
            session_model_context=restored.model_context,
            cache_epoch=restored.cache_epoch,
            rollout_recorder=SessionRolloutRecorder(store, session),
        )

    def new_session(title: str) -> Session:
        session = Session.new(
            title=title,
            persona="default",
            model=config.model_id,
            reasoning_preset=config.reasoning_preset,
            persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        )
        store.create(session)
        return session

    session = new_session("144 synthetic task compaction")
    conversation = open_conversation(session, force_tool_boundary=True)
    row: dict[str, Any] = {
        "scenario": "task_compaction",
        "trigger": "synthetic_force_at_completed_tool_boundary",
    }
    report["scenarios"].append(row)
    reply = conversation.send(
        GOAL + "\n先且仅调用一次 checkpoint_fixture 读取合成记录，再按最新目标回答。"
    )
    _observe(conversation, reply, row)
    assert fixture.invocations == 1, "fixture_action_repeated_or_missing"
    assert row["compactions"], "task_did_not_compact"
    assert row["compactions"][-1]["version"] == 4, "current_task_not_replaced"
    row["budget_pressure"] = "synthetic_retained_fixture_result_over_threshold"
    row["in_memory_actions"] = fixture.invocations

    client.stage = "explicit_recovery"
    session = new_session("144 synthetic failed task")
    _append(session, store, "run_started", "seed-start", run_id="seeded-run")
    _append(session, store, "user_message", "seed-goal", content=GOAL)
    _append(
        session,
        store,
        "tool_call_request",
        "seed-call",
        tool_call_id="seed-call",
        tool_name=fixture.name,
        args={},
    )
    _append(
        session,
        store,
        "tool_call_result",
        "seed-result",
        tool_call_id="seed-call",
        tool_name=fixture.name,
        content=fixture_result(6_000),
        is_error=False,
        executed=True,
    )
    _append(
        session,
        store,
        "run_error",
        "seed-error",
        code="context_compaction_unavailable",
        message="Synthetic pre-provider summary failure",
        recoverable=True,
        run_id="seeded-run",
    )
    _append(session, store, "run_terminal", "seed-terminal", status="failed", run_id="seeded-run")
    conversation = open_conversation(session)
    row = {"scenario": "explicit_recovery", "failure_source": "synthetic_pre_provider_fixture"}
    report["scenarios"].append(row)
    events = list(
        conversation.continue_after_tool_error(
            run_id="recovered-run",
            source_run_id="seeded-run",
            recovery_mode="compact_then_resume",
        )
    )
    reply = "".join(
        getattr(event, "text", "") for event in events if getattr(event, "type", "") == "text_delta"
    )
    _observe(conversation, reply, row)
    assert row["compactions"], "explicit_recovery_did_not_compact"
    assert fixture.invocations == 1, "recovery_repeated_fixture_action"
    row["in_memory_actions"] = fixture.invocations
    checkpoint_ids = [item["uuid"] for item in row["compactions"]]

    client.stage = "cold_recovery"
    cold = open_conversation(store.load(session.session_id))
    row = {"scenario": "cold_recovery", "reconstructed_conversation_and_rollout": True}
    report["scenarios"].append(row)
    reply = cold.send(
        "仅复核刚才保存的最新目标、约束及 A/B/C/D 四种状态，使用相同 JSON 格式。不调用工具。"
    )
    _observe(cold, reply, row)
    assert [item["uuid"] for item in row["compactions"]] == checkpoint_ids, "checkpoint_not_reused"
    assert fixture.invocations == 1, "cold_recovery_repeated_fixture_action"
    row["in_memory_actions"] = fixture.invocations
    report["no_repeated_in_memory_actions"] = True


def run_long_session(
    inner: LanguageModel,
    *,
    config: ModelRuntimeConfig,
    output: Path,
    connection_name: str,
    model_label: str,
    real_provider: bool = True,
) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("observation_output_must_be_outside_repository")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as target:
        report: dict[str, Any] = {
            "schema": "agent-friend.long-session-observation.v1",
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "source": source_identity(),
            "connection": connection_name,
            "model": model_label,
            "reasoning_preset": config.reasoning_preset,
            "logical_context_window": min(inner.context_window, 20_000),
            "expected_requests": 6,
            "request_limit": MAX_REQUESTS,
            "requests": [],
            "scenarios": [],
            "expected_state": EXPECTED,
            "real_provider_observation": real_provider,
            "scope": "synthetic session; local tool only changes an in-memory counter; no background services",
        }
        try:
            with TemporaryDirectory(prefix="af-144-observation-", dir=output.parent) as temporary:
                _exercise(BoundedModel(inner, report), config, Path(temporary), report)
            report["status"] = "passed"
        except Exception as error:
            report["status"] = "failed"
            report["error_type"] = type(error).__name__
            # Provider exception strings may contain private connection details.
        finally:
            report["request_count"] = len(report["requests"])
            report["finished_at"] = datetime.now(UTC).isoformat()
            target.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report

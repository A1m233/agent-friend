"""Bounded real-provider smoke for input anchors, tool continuation and rollout reload."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.context.budget import make_budget_snapshot
from agent.sessions.rollout import SessionRolloutRecorder, restore_rollout
from agent.tools.builtin.calculate import CalculateTool
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from llm_providers.binding import bind_language_model

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    Session,
    SummarizingContextManager,
    ToolRegistry,
)
from llm_providers import (
    LanguageModel,
    LanguageModelCapabilities,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    default_model_runtime_config,
    get_model_capabilities,
)
from runtime_config import credential_source_for_mode

ROOT = Path(__file__).resolve().parents[2]
FIELDS = (
    "token_estimate",
    "raw_request_tokens",
    "trigger_threshold",
    "effective_window",
    "estimation_mode",
    "anchor_input_tokens",
    "incremental_tokens",
    "anchor_fallback_reason",
)


class SmokeClient:
    """Forward bound model identity/capabilities; cap all actual calls at five."""

    def __init__(self, inner: LanguageModel, report: dict[str, Any]) -> None:
        self.inner = inner
        self.report = report
        self.context_window = inner.context_window
        self.spec = getattr(inner, "spec", None)
        self.capabilities: LanguageModelCapabilities = get_model_capabilities(inner)
        self.conversation: Conversation | None = None
        self.stage = "warmup"

    def generate(self, request: ModelRequest) -> ModelResponse:
        # Unexpected summaries invalidate this particular no-premature-compaction
        # test. Refuse before sending rather than expanding the paid call budget.
        raise AssertionError("unexpected_compaction_attempt")

    def stream(self, request: ModelRequest) -> Iterator[Any]:
        calls = self.report["calls"]
        if len(calls) >= 5:
            raise AssertionError("smoke_call_budget_exceeded")
        assert self.conversation is not None
        notes = self.conversation.last_context_notes
        row: dict[str, Any] = {
            "stage": self.stage,
            "before": {key: notes.get(key) for key in FIELDS},
        }
        calls.append(row)
        bounded = replace(
            request,
            settings={**request.settings, "max_tokens": 2048},
            control=replace(request.control, allow_retries=False),
        )
        for event in self.inner.stream(bounded):
            if isinstance(event, LLMTurnDone):
                if event.usage is not None:
                    row["actual_input"] = event.usage.prompt_tokens
                    row["actual_output"] = event.usage.completion_tokens
                    row["accounting"] = event.usage.input_token_accounting
                    row["cached_input"] = event.usage.cached_prompt_tokens
                row["stop_reason"] = event.stop_reason
            yield event


def source_identity() -> dict[str, Any]:
    paths = [
        "agent/src/agent/context/budget.py",
        "agent/src/agent/context/protocol.py",
        "agent/src/agent/context/usage_anchor.py",
        "agent/src/agent/conversation.py",
        "llm_providers/src/llm_providers/protocol.py",
        "llm_providers/src/llm_providers/client.py",
        "scripts/context-usage-smoke/run.py",
    ]
    return {
        "base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip(),
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
        },
        "worktree_overlay": True,
    }


def run(model: str, report: dict[str, Any]) -> None:
    runtime = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    config = default_model_runtime_config(model)
    configured = runtime.llm_client_factory(config)
    with (
        bind_language_model(configured) as bound,
        tempfile.TemporaryDirectory(
            prefix="agent-friend-usage-smoke-",
        ) as temporary,
    ):
        if get_model_capabilities(bound).input_usage_scope != "request":
            raise AssertionError("model_does_not_support_request_input_anchor")
        root = Path(temporary)
        store = JsonlSessionStore(root / "sessions")
        catalog = PersonaCatalog(external_dir=root / "personas")
        builder = MarkdownPromptBuilder(persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog)
        session = Session.new(
            title="123 isolated usage smoke",
            persona="default",
            model=model,
            persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        )
        store.create(session)
        client = SmokeClient(bound, report)
        tools = ToolRegistry([CalculateTool()])
        conversation = Conversation(
            session=session,
            store=store,
            llm_client=client,
            prompt_builder=builder,
            catalog=catalog,
            context_manager=SummarizingContextManager(),
            tool_registry=tools,
            rollout_recorder=SessionRolloutRecorder(store=store, session=session),
        )
        client.conversation = conversation
        report["provider_context_window"] = bound.context_window
        report["model_config"] = {
            "model_id": config.model_id,
            "reasoning_preset": config.reasoning_preset,
        }
        # Repetitive synthetic ASCII deliberately exposes the character heuristic's
        # overestimate; no user history, memory, attachments, or external tools.
        unit = "Synthetic inventory: oak table, blue chair, copper lamp. No actions required.\n"
        history = (unit * 600)[:40_000]
        first = conversation.send(history + "\n以上只是测试背景，不要调用工具，只回复 READY。")
        if "READY" not in first:
            raise AssertionError("warmup_response_not_ready")
        measured = report["calls"][-1].get("actual_input", 0)
        raw = conversation.last_context_notes["raw_request_tokens"]
        if not isinstance(measured, int) or measured <= 0 or raw < measured * 1.25:
            raise AssertionError("insufficient_real_estimation_gap_for_stress_test")
        # Pick a logical test window between real usage and old full estimation.
        target_threshold = (raw + measured) // 2
        client.context_window = int(target_threshold / 0.8)
        threshold = make_budget_snapshot(client.context_window, measured).trigger_threshold
        if not measured + 1000 < threshold < raw:
            raise AssertionError("invalid_stress_window")
        report["test_window"] = client.context_window
        report["test_threshold"] = threshold
        report["synthetic_history_chars"] = len(history)

        client.stage = "tool_turn"
        answer = conversation.send(
            "现在必须调用一次 calculate，operation=evaluate，expression=17*23。"
            "得到结果后只回复结果数字，不要再调用工具。"
        )
        if "391" not in answer:
            raise AssertionError("calculation_answer_missing")
        tool_calls = [row for row in report["calls"] if row["stage"] == "tool_turn"]
        if len(tool_calls) != 2 or tool_calls[0].get("stop_reason") != "tool_use":
            raise AssertionError("expected_one_real_tool_continuation")
        for row in tool_calls:
            if row["before"]["estimation_mode"] != "usage_anchor":
                raise AssertionError("tool_turn_did_not_reuse_anchor")
            if (
                not row["before"]["token_estimate"]
                < threshold
                < row["before"]["raw_request_tokens"]
            ):
                raise AssertionError("stress_test_did_not_distinguish_old_estimator")

        reloaded_store = JsonlSessionStore(root / "sessions")
        reloaded = reloaded_store.load(session.session_id)
        restored = restore_rollout(reloaded)
        restarted_client = SmokeClient(bound, report)
        restarted_client.context_window = client.context_window
        restarted_client.stage = "rollout_reload"
        restarted = Conversation(
            session=reloaded,
            store=reloaded_store,
            llm_client=restarted_client,
            prompt_builder=builder,
            catalog=catalog,
            context_manager=SummarizingContextManager(),
            tool_registry=tools,
            session_model_history=restored.history,
            session_model_context=restored.model_context,
            cache_epoch=restored.cache_epoch,
            rollout_recorder=SessionRolloutRecorder(store=reloaded_store, session=reloaded),
        )
        restarted_client.conversation = restarted
        reply = restarted.send("不调用工具，只复述刚才计算结果的数字。")
        if "391" not in reply:
            raise AssertionError("reload_answer_missing")
        if restarted.last_context_notes["estimation_mode"] != "usage_anchor":
            raise AssertionError("reloaded_anchor_not_used")
        if any(event.type == "compaction" for event in reloaded.events):
            raise AssertionError("unexpected_persisted_compaction")
        if len(report["calls"]) != 4:
            raise AssertionError("unexpected_call_count")
        report["status"] = "passed"
        report["persisted_compactions"] = 0
        report["reload_kind"] = "new_Conversation_and_store_from_verified_rollout_same_process"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--model", default="kimi/kimi-k2.7-code-highspeed")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error(
            "--allow-llm is required: expected 4 real requests, hard cap 5, no automatic retries"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve a unique artifact before making paid calls; never overwrite a run.
    with args.output.open("x", encoding="utf-8") as target:
        report: dict[str, Any] = {
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "source": source_identity(),
            "calls": [],
            "scope": "isolated synthetic text; logical window override; no product settings changed",
        }
        try:
            run(args.model, report)
        except Exception as error:
            report["status"] = "failed"
            report["error_type"] = type(error).__name__
            if isinstance(error, AssertionError):
                report["check_failure"] = str(error)
        report["finished_at"] = datetime.now(UTC).isoformat()
        target.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

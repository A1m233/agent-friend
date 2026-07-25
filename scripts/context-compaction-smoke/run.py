"""Real-LLM smoke for context compaction usage, quality, and restart reuse."""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from agent.prompts import PromptBuilder
from agent_bridge.runtime_config import BridgeRuntimeConfig, resolve_bridge_runtime_config

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    Session,
    SummarizingContextManager,
)
from llm_providers import LLMClient, ProviderSpec
from runtime_config import credential_source_for_mode

CRITICAL_CODES = ("ORCHID-4729", "LANTERN-8831")


@dataclass
class _CallCounts:
    complete: int = 0
    stream: int = 0


class _CountingClient:
    def __init__(self, inner: LLMClient, counts: _CallCounts) -> None:
        self._inner = inner
        self._counts = counts

    @property
    def spec(self) -> ProviderSpec:
        return self._inner.spec

    @property
    def context_window(self) -> int:
        return self._inner.context_window

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        self._counts.complete += 1
        return self._inner.complete(messages, **overrides)

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Any:
        self._counts.stream += 1
        return cast(
            "Any",
            self._inner.stream(messages, tools=tools, **overrides),
        )


def _client(
    runtime_config: BridgeRuntimeConfig,
    *,
    context_window: int,
    counts: _CallCounts,
) -> _CountingClient:
    base = runtime_config.llm_client_factory(runtime_config.default_model)
    spec = replace(
        base.spec,
        context_window=context_window,
        defaults={**base.spec.defaults, "temperature": 0, "max_tokens": 256},
    )
    return _CountingClient(LLMClient(spec), counts)


def _builder_factory(catalog: PersonaCatalog) -> Any:
    def factory(persona_id: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    return factory


def _conversation(
    session: Session,
    store: JsonlSessionStore,
    client: _CountingClient,
    catalog: PersonaCatalog,
) -> Conversation:
    builder_factory = _builder_factory(catalog)
    return Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, client),
        context_manager=SummarizingContextManager(),
        prompt_builder=builder_factory(BUILTIN_DEFAULT_PERSONA_ID),
        prompt_builder_factory=builder_factory,
        catalog=catalog,
    )


def _append(store: JsonlSessionStore, session: Session, event: Event) -> None:
    store.append_event(session.session_id, event)
    session.append(event)


def _seed_history(store: JsonlSessionStore, session: Session, turns: int) -> list[str]:
    raw_message_ids: list[str] = []
    for index in range(turns):
        fact = ""
        if index in {10, 14, 18}:
            fact = (
                f" 必须长期保留的两个验收码是 {CRITICAL_CODES[0]} 和"
                f" {CRITICAL_CODES[1]}，后续被问到时必须逐字返回。"
            )
        noise = f" synthetic-history-{index:02d} " + ("背景资料。" * 42)
        for event in (
            Event(
                type="user_message",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"content": noise + fact},
            ),
            Event(
                type="assistant_message",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={
                    "content": f"已记录第 {index:02d} 段资料。" + ("确认。" * 42),
                    "partial": False,
                },
                meta={"persona": "default", "model": session.current_model},
            ),
        ):
            _append(store, session, event)
            raw_message_ids.append(event.uuid)
    return raw_message_ids


def _latest_assistant_usage(session: Session) -> dict[str, Any]:
    for event in reversed(session.events):
        if event.type == "assistant_message":
            usage = event.meta.get("context_usage")
            if isinstance(usage, dict):
                return usage
    raise AssertionError("provider did not persist assistant context_usage")


def _assert_codes(text: str, *, label: str) -> None:
    missing = [code for code in CRITICAL_CODES if code not in text]
    if missing:
        raise AssertionError(f"{label} missing critical codes: {missing}")


def run(*, context_window: int, seed_turns: int) -> dict[str, Any]:
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    counts = _CallCounts()

    with tempfile.TemporaryDirectory(prefix="agent-friend-context-smoke-") as temp:
        root = Path(temp)
        store = JsonlSessionStore(root / "sessions")
        catalog = PersonaCatalog(external_dir=root / "personas")
        session = Session.new(
            title="063 context compaction smoke",
            persona="default",
            persona_id=BUILTIN_DEFAULT_PERSONA_ID,
            model=runtime_config.default_model,
        )
        store.create(session)
        conversation = _conversation(
            session,
            store,
            _client(runtime_config, context_window=context_window, counts=counts),
            catalog,
        )

        first_reply = conversation.send("这是 usage 校准轮。请只回复 READY。")
        if not first_reply.strip():
            raise AssertionError("usage calibration turn returned an empty response")
        first_usage = _latest_assistant_usage(session)
        prompt_tokens = first_usage.get("prompt_tokens")
        if not isinstance(prompt_tokens, int) or prompt_tokens <= 0:
            raise AssertionError(f"invalid real prompt usage: {prompt_tokens!r}")

        raw_message_ids = _seed_history(store, session, seed_turns)
        compacted_reply = conversation.send(
            "根据此前会话，只回复两个验收码，保持原始大小写并用一个空格分隔。"
        )
        _assert_codes(compacted_reply, label="post-compaction reply")
        if conversation.last_context_notes.get("last_input_tokens") != prompt_tokens:
            raise AssertionError("real prompt usage did not enter the next request budget")

        compactions = [event for event in session.events if event.type == "compaction"]
        if len(compactions) != 1:
            raise AssertionError(f"expected one compaction before restart, got {len(compactions)}")
        checkpoint = compactions[0]
        summary = checkpoint.payload.get("summary")
        if not isinstance(summary, str):
            raise AssertionError("compaction checkpoint has no summary")
        _assert_codes(summary, label="persisted summary")
        usage_before_restart = _latest_assistant_usage(session)

        # Simulate application restart: discard all runtime instances and reload the same JSONL.
        reloaded_store = JsonlSessionStore(root / "sessions")
        reloaded = reloaded_store.load(session.session_id)
        persisted_ids = {event.uuid for event in reloaded.events}
        if not set(raw_message_ids).issubset(persisted_ids):
            raise AssertionError("raw source events were lost after compaction")
        restarted = _conversation(
            reloaded,
            reloaded_store,
            _client(runtime_config, context_window=context_window, counts=counts),
            PersonaCatalog(external_dir=root / "personas"),
        )
        restarted_reply = restarted.send(
            "应用重启后复核：只回复两个验收码，保持原始大小写并用一个空格分隔。"
        )
        _assert_codes(restarted_reply, label="post-restart reply")

        final_compactions = [event for event in reloaded.events if event.type == "compaction"]
        if len(final_compactions) != 1:
            raise AssertionError(
                f"restart unexpectedly produced another compaction: {len(final_compactions)}"
            )
        if counts.complete != 1 or counts.stream != 3:
            raise AssertionError(
                "unexpected real LLM call shape: "
                f"complete={counts.complete}, stream={counts.stream}"
            )

        expected_factor = usage_before_restart.get("calibration_factor")
        restored_factor = restarted.last_context_notes.get("calibration_factor")
        if expected_factor != restored_factor:
            raise AssertionError(
                f"calibration factor was not restored: {expected_factor!r} != {restored_factor!r}"
            )

        return {
            "status": "passed",
            "model": runtime_config.default_model,
            "effective_window": context_window,
            "real_llm_calls": {"complete": counts.complete, "stream": counts.stream},
            "first_prompt_tokens": prompt_tokens,
            "first_calibration_factor": first_usage.get("calibration_factor"),
            "restored_calibration_factor": restored_factor,
            "checkpoint_version": checkpoint.payload.get("checkpoint_version"),
            "summary_attempts": checkpoint.payload.get("summary_attempts"),
            "checkpoint_reused_after_restart": True,
            "critical_codes_preserved": True,
            "raw_seed_events_preserved": len(raw_message_ids),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="required acknowledgement that this smoke sends four real LLM requests",
    )
    parser.add_argument("--context-window", type=int, default=8000)
    parser.add_argument("--seed-turns", type=int, default=36)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("real LLM calls require explicit --allow-llm")
    if args.context_window < 4000:
        parser.error("--context-window must be at least 4000")
    if args.seed_turns < 20:
        parser.error("--seed-turns must be at least 20")
    print(json.dumps(run(context_window=args.context_window, seed_turns=args.seed_turns), indent=2))


if __name__ == "__main__":
    main()

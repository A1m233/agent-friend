"""Bounded real-provider smoke of memory, compaction, continuation and notifications."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import threading
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.context import RuntimeContext, SummarizingContextManager, make_budget_snapshot
from agent.messages import Message
from agent.notifications import NotificationFact
from agent.personas import EffectivePersonaStore
from agent_bridge.notifications.generation import NotificationGenerator
from memory.extraction import ExtractionContextBuilder, Reconciler
from memory.store import SemanticRow

from llm_providers import (
    LLMClient,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ProviderSpec,
    build_provider_spec,
    minimum_model_runtime_config,
)
from memory import (
    ConversationFragment,
    Extractor,
    SemanticUpdateContext,
    SemanticUpdateTurn,
    SqliteMemoryStore,
    Utterance,
)
from runtime_config import DesktopSettingsReader, credential_source_for_mode, require_secret

ROOT = Path(__file__).resolve().parents[2]
ROUTES = {
    "deepseek-responses": ("deepseek", "deepseek/deepseek-v4-flash", "DEEPSEEK_API_KEY"),
    "deepseek-chat": ("deepseek", "deepseek/deepseek-v4-flash", "DEEPSEEK_API_KEY"),
    "kimi-responses": ("kimi", "kimi/kimi-k3", "KIMI_API_KEY"),
    "kimi-chat": ("kimi", "kimi/kimi-k2.6", "KIMI_API_KEY"),
}


class ObservedModel:
    def __init__(self, model: LLMClient) -> None:
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self.context_window = model.context_window
        self.capabilities = model.capabilities

    def _request(self, request: ModelRequest) -> ModelRequest:
        if len(self.calls) >= 10:
            raise RuntimeError("smoke request budget exhausted")
        trace = request.control.trace_context
        self.calls.append(
            {
                "operation": trace.operation if trace else "continuation",
                "kind": trace.call_kind if trace else "continuation",
            }
        )
        return replace(
            request,
            settings={"max_tokens": 8192, **request.settings},
            control=replace(request.control, allow_retries=False),
        )

    def _record(self, result: Any) -> None:
        if result.diagnostics:
            self.calls[-1].update(
                transport=result.diagnostics.transport,
                response_model=result.diagnostics.response_model,
                fingerprint=result.diagnostics.request_fingerprint,
            )
        self.calls[-1]["usage"] = asdict(result.usage) if result.usage else None

    def generate(self, request: ModelRequest) -> ModelResponse:
        response = self.model.generate(self._request(request))
        self._record(response)
        return response

    def stream(self, request: ModelRequest):
        for event in self.model.stream(self._request(request)):
            if isinstance(event, LLMTurnDone):
                self._record(event)
            yield event


def run(
    route: str,
    root: Path,
    stage: str = "all",
    custom: bool = False,
    spec_override: ProviderSpec | None = None,
) -> dict[str, Any]:
    if spec_override is not None:
        custom = True
        assert spec_override.product_model_id is not None
        provider, model_id = "custom", spec_override.product_model_id
        spec = spec_override
    else:
        provider, model_id, key_name = ROUTES[route]
        source = credential_source_for_mode("desktop")
        spec = build_provider_spec(
            provider_id=provider,
            model_id=model_id,
            api_key=require_secret(source, key_name),
            reasoning_preset=minimum_model_runtime_config(model_id).reasoning_preset,
            timeout_seconds=90,
        )
        if route.endswith("-chat"):
            spec = replace(spec, responses=None)
        if custom:
            from uuid import UUID

            from llm_providers.connections import (
                ConnectionDefinition,
                CustomModel,
                ModelDirectory,
                model_reference,
            )

            connection = ConnectionDefinition(
                str(UUID(int=121)),
                "Background smoke",
                "https://api.deepseek.com"
                if provider == "deepseek"
                else "https://api.moonshot.cn/v1",
                "responses" if route.endswith("responses") else "chat_completions",
                (CustomModel(str(UUID(int=122)), model_id.split("/", 1)[1], 1_000_000),),
            )
            directory = ModelDirectory([connection])
            model_id = model_reference(connection.id, connection.models[0].id)
            spec = directory.build_spec(
                directory.default_config(model_id), api_key=require_secret(source, key_name)
            )
            spec = replace(spec, defaults={**spec.defaults, "timeout": 90})
            provider = "custom"
    model = ObservedModel(LLMClient(spec, network_retries=0, engine_overload_retries=0))
    report: dict[str, Any] = {
        "route": route,
        "model": model_id,
        "custom_connection": custom,
        "stages": {},
        "calls": model.calls,
    }
    report["compaction_fixture"] = {
        "effective_window": 100_000 if custom else 3000,
        "emergency_ratio": 0.03 if custom else 1.0,
        "trigger_threshold": 2400,
        "summary_output_budget": 4096 if custom else 512,
    }
    now = datetime(2026, 9, 9, tzinfo=UTC)
    store = SqliteMemoryStore(str(root / "memory.db"))
    try:
        if stage != "compaction-notification":
            first = Utterance(
                speaker="user",
                text="以后请叫我小岚，我目前住在深圳。我每周游泳三次。",
                ts=now,
                source_ref="smoke#first",
            )
            fragment = ConversationFragment(session_id="smoke", persona_id="p1", utterances=[first])
            context = ExtractionContextBuilder(store).build(fragment)
            attempts: list[Any] = []
            output = Extractor(model, on_attempt=attempts.append).extract(fragment, context)
            result = Reconciler(store).apply(output, fragment, context)
            profile = store.core_profile(owner_user_id="local", persona_id="p1").as_dict()
            assert result.committed and profile["user"]["preferred_name"] == "小岚", (
                "memory profile was not persisted"
            )
            report["stages"]["memory_base"] = {
                "passed": True,
                "profile": profile,
                "attempts": [{"phase": a.phase, "status": a.status} for a in attempts],
            }

            # A known prior fact guarantees a visible reconciliation target without depending on base classification.
            store.add_semantic(
                SemanticRow(
                    id="known-frequency",
                    statement="用户每周游泳三次",
                    persona_id="p1",
                    created_at=now,
                    updated_at=now,
                    valid_from=now,
                )
            )
            correction = ConversationFragment(
                session_id="smoke",
                persona_id="p1",
                utterances=[
                    Utterance(
                        speaker="user",
                        text="更正一下，刚才说错了，我现在每周只游泳一次，不是三次。",
                        ts=now,
                        source_ref="smoke#correction",
                    )
                ],
                semantic_update_context=SemanticUpdateContext(
                    turns=(
                        SemanticUpdateTurn(
                            utterances=(
                                first,
                                Utterance(
                                    speaker="agent",
                                    text="记住了，你每周游泳三次。",
                                    ts=now,
                                    source_ref="smoke#first-agent",
                                ),
                            )
                        ),
                    )
                ),
            )
            context = ExtractionContextBuilder(store).build(correction)
            attempts.clear()
            output = Extractor(model, on_attempt=attempts.append).extract(correction, context)
            result = Reconciler(store).apply(output, correction, context)
            facts = [row.statement for row in store.list_semantic(owner_user_id="local")]
            assert result.committed and any(
                a.phase == "semantic_reconciliation" for a in attempts
            ), "reconciliation was not executed"
            assert any("游泳" in fact and "一次" in fact for fact in facts), (
                "corrected frequency missing"
            )
            assert not any("游泳三次" in fact for fact in facts), "retired frequency still active"
            report["stages"]["memory_reconciliation"] = {
                "passed": True,
                "facts": facts,
                "attempts": [{"phase": a.phase, "status": a.status} for a in attempts],
            }

        history = []
        for i in range(12):
            history.extend(
                [
                    Message(
                        role="user",
                        content="项目代号是 cobalt-73，交付地点是深圳。"
                        + "我们正在整理本周的资料，需要保留项目代号。" * 12,
                        uuid=f"u{i}",
                    ),
                    Message(
                        role="assistant",
                        content="记住了项目代号 cobalt-73，交付地点深圳。"
                        + "我会继续整理资料并保持信息一致。" * 12,
                        uuid=f"a{i}",
                    ),
                ]
            )
        compacted = SummarizingContextManager().build_messages(
            history=history,
            system_prompt="请用中文协助用户。",
            runtime=RuntimeContext(
                budget=(
                    replace(make_budget_snapshot(100_000, None), emergency_ratio=0.03)
                    if custom
                    else make_budget_snapshot(3000, None)
                ),
                llm_client=model,
                model_id=model_id,
                session_id="smoke-compaction",
            ),
        )
        assert compacted.new_compaction is not None, "compaction did not run or fell back"
        messages = [{"role": item.role, "content": item.content} for item in compacted.messages]
        continuation = model.generate(
            ModelRequest(
                messages=[*messages, {"role": "user", "content": "我们项目的代号是什么？"}]
            )
        )
        assert "cobalt-73" in continuation.text, "compacted continuation lost anchor"
        report["stages"]["compaction"] = {
            "passed": True,
            "summary_chars": len(compacted.new_compaction.summary),
            "continuation": continuation.text,
        }

        reader = DesktopSettingsReader(root / "settings.json")
        reader.path.write_text(
            json.dumps({"chatModel": {"provider": provider, "model": model_id}}), encoding="utf-8"
        )
        persona = EffectivePersonaStore(
            user_file=root / "persona.md",
            internal_dir=root / "internal",
            builtin_loader=lambda: "你是一位自然、简洁的中文助手。",
        )
        notification = NotificationGenerator(
            reader=reader, factory=lambda _: model, persona=persona, memory=None
        )
        text = notification.generate(
            NotificationFact(
                event_key="smoke-ready",
                kind="reply_ready",
                title="整理资料",
                task="整理项目资料",
                reply="资料已经整理完成。",
                persona_id="p1",
            ),
            stopping=threading.Event(),
            still_valid=lambda: True,
        )
        assert text and len(text) <= 160
        report["stages"]["notification"] = {"passed": True, "text": text}
        expected_transport = "responses" if route.endswith("responses") else "litellm"
        assert all(
            call.get("transport")
            in (
                {expected_transport}
                if expected_transport == "responses"
                else {"litellm", "kimi_direct"}
            )
            for call in model.calls
        ), "missing or incorrect request route evidence"
        report["passed"] = True
    except Exception as error:
        report["passed"] = False
        report["error"] = {
            "type": type(error).__name__,
            "assertion": str(error) if isinstance(error, AssertionError) else None,
        }
    finally:
        store.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--route", choices=[*ROUTES, "all"], default="all")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=["all", "compaction-notification"], default="all")
    parser.add_argument("--custom", action="store_true")
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("explicit --allow-llm required; at most 10 calls per route")
    sources = [
        *ROOT.glob("llm_providers/src/**/*.py"),
        *ROOT.glob("agent/src/**/*.py"),
        *ROOT.glob("agent_bridge/src/**/*.py"),
        *ROOT.glob("memory/src/**/*.py"),
        Path(__file__),
    ]
    report = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
        },
        "routes": [],
    }
    with args.output.open("x", encoding="utf-8") as output:
        try:
            for route in ROUTES if args.route == "all" else [args.route]:
                with tempfile.TemporaryDirectory(prefix="agent-friend-model-smoke-") as temporary:
                    report["routes"].append(run(route, Path(temporary), args.stage, args.custom))
        finally:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    passed = all(route["passed"] for route in report["routes"])
    print(json.dumps({"passed": passed, "output": str(args.output.resolve())}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

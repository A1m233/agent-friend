"""Bounded CPA smoke: low/medium usage continuity and failed-edit cold recovery."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.context import SummarizingContextManager
from agent.context.errors import ContextCompactionUnavailableError
from agent.sessions import SessionRolloutRecorder, restore_rollout
from agent_bridge.runtime_config import resolve_bridge_runtime_config

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    PersonaCatalog,
    Session,
)
from llm_providers import ModelRuntimeConfig
from runtime_config import credential_source_for_mode


class UnavailableContext(SummarizingContextManager):
    def stream_build_messages(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise ContextCompactionUnavailableError("synthetic pre-provider failure")
        yield


def main() -> None:
    if sys.argv[1:2] == ["--desktop-stage"]:
        raise SystemExit(
            subprocess.call(["node", str(Path(__file__).with_name("desktop.mjs")), *sys.argv[2:]])
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--model", required=True)
    parser.add_argument("--connection-name", default="CPA")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_llm or args.output.exists():
        parser.error("Requires --allow-llm and a new output path")
    config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    assert config.connection_runtime is not None
    directory = config.connection_runtime.directory()
    parts = args.model.split("/")
    if len(parts) != 3 or parts[0] != "custom":
        parser.error("Only a configured CPA custom connection is allowed")
    connection = directory.connections[parts[1]]
    if connection.name.casefold() != args.connection_name.casefold():
        parser.error("Selected connection does not match the explicitly named CPA connection")
    factory = config.llm_client_factory
    report: dict = {
        "connection": connection.name,
        "model": directory.model(args.model).label,
        "calls": [],
    }
    with TemporaryDirectory(prefix="af-context-recovery-") as root:
        store = JsonlSessionStore(Path(root) / "sessions")
        catalog = PersonaCatalog(external_dir=Path(root) / "personas")
        builder = MarkdownPromptBuilder(persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog)
        session = Session.new(
            title="Synthetic context recovery smoke",
            persona="default",
            model=args.model,
            persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        )
        store.create(session)

        def open_conversation(current: Session) -> Conversation:
            rollout = restore_rollout(current)
            return Conversation(
                session=current,
                store=store,
                llm_client=factory(current.current_model_config),
                llm_client_factory=factory,
                prompt_builder=builder,
                catalog=catalog,
                context_manager=SummarizingContextManager(),
                session_model_history=rollout.history,
                session_model_context=rollout.model_context,
                cache_epoch=rollout.cache_epoch,
                rollout_recorder=SessionRolloutRecorder(store, current),
            )

        conv = open_conversation(session)
        conv.switch_model_config(ModelRuntimeConfig(args.model, "effort:low"))
        for preset in ("low", "medium"):
            conv.switch_model_config(ModelRuntimeConfig(args.model, "effort:" + preset))
            text = conv.send(
                "这是隔离验收。当前任务是核对标记 TASK137。请只回复 TASK137，不调用工具。"
            )
            assert "TASK137" in text
            report["calls"].append(
                {"preset": preset, "reply": text, "notes": conv.last_context_notes}
            )
        assert conv.last_context_notes["estimation_mode"].startswith("usage_anchor")
        conv._context_manager = UnavailableContext()
        try:
            list(conv.edit_resend_latest("新任务：请只回复 REPLACEMENT137，不调用工具。"))
        except ContextCompactionUnavailableError:
            pass
        else:
            raise AssertionError("failure injection did not reach context build")
        cold = store.load(session.session_id)
        restarted = open_conversation(cold)
        text = restarted.send("请继续完成上一条用户请求，只输出该请求要求的标记。")
        assert "REPLACEMENT137" in text and "TASK137" not in text
        report["calls"].append(
            {"preset": "medium", "reply": text, "notes": restarted.last_context_notes}
        )
        report["real_conversation_turns"] = 3
        report["compactions"] = sum(e.type == "compaction" for e in cold.events)
        assert report["compactions"] == 0
        report["passed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": True, "real_conversation_turns": 3, "output": str(args.output)}))


if __name__ == "__main__":
    main()

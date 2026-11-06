"""Offline execution of packaged prompt, index, controls and prepared-message paths."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory


def run_companionship_smoke() -> dict[str, bool]:
    from agent.approvals.reviewer import SecurityReviewer
    from agent.companionship.snapshot import capture_changes, render_overview
    from agent.companionship.store import CompanionshipStore
    from agent.sessions.access.service import ConversationAccessService
    from agent.sessions.changes import SessionChangeBroker, SessionRevisionStore
    from agent.sessions.store import NotifyingSessionStore

    from agent import JsonlSessionStore, Session, SessionManager
    from llm_providers import LLMStreamEvent, ModelRequest, ModelResponse

    from .social import run_social_smoke

    class Model:
        context_window = 128000

        def generate(self, request: ModelRequest) -> ModelResponse:
            prompt = str(request.messages[0]["content"])
            if "Data Exfiltration" not in prompt or "{{ tenant_policy_config }}" in prompt:
                raise RuntimeError("packaged security policy not assembled")
            return ModelResponse('{"outcome":"allow"}')

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            return iter(())

    now = datetime.now(UTC)
    with TemporaryDirectory(prefix="af-companionship-smoke-") as temp:
        root = Path(temp)
        store = NotifyingSessionStore(
            JsonlSessionStore(root / "sessions"),
            SessionChangeBroker(SessionRevisionStore(root / "revisions.db")),
        )
        store.create(Session.new("smoke", "default", "test/model", session_id="target"))
        manager = SessionManager(store)
        manager.append_delivered_message(
            "target",
            delivery_id="smoke",
            content="prepared",
            source_session_id="inner",
            source_run_id="wake",
        )
        access = ConversationAccessService(store)
        state = CompanionshipStore(root / "state.db")
        state.control("enable", now=now)
        snapshot = capture_changes(access, state.read(), "wake")
        if render_overview(access, snapshot)["conversations"][0]["preview"] != "prepared":
            raise RuntimeError("packaged direct conversation read failed")
        state.begin("wake", snapshot, now=now)
        state.record_call("wake", now=now)
        if state.daily_calls(now.date().isoformat()) != 1:
            raise RuntimeError("packaged wake usage was not recorded")
        assessment = SecurityReviewer(Model()).review(
            {"kind": "filesystem"},
            [],
            inspect=lambda *_: {},
            cancel_check=lambda: None,
            deadline=time.monotonic() + 5,
            session_id="inner",
            run_id="wake",
        )
        if assessment.outcome != "allow":
            raise RuntimeError("packaged reviewer failed")
        prompt = files("agent.companionship").joinpath("activity.md").read_text(encoding="utf-8")
        if "不是场景清单" not in prompt:
            raise RuntimeError("packaged autonomous prompt absent")
        manager.close()
    return {
        **run_social_smoke(),
        "autonomous_prompt": True,
        "security_review": True,
        "direct_history": True,
        "prepared_delivery": True,
        "durable_wake_usage": True,
    }

"""Synthetic display/contact fixtures; no production data or external messaging."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent.message_exposure import content_version

from agent import Event

EXPOSURE_CASES = {
    "exposure_full": "有空帮我查一下周六社区旧书交换几点结束，查到后留在这个会话就好。",
    "exposure_unknown": "有空帮我查一下周六社区旧书交换几点结束，查到后留在这个会话就好。",
    "exposure_shared": "有空帮我查一下周六社区旧书交换几点结束，查到后留在这个会话就好。",
    "exposure_waiting": "我现在要出门了，帮我查一下周六社区旧书交换几点结束。查到后主动来告诉我，我正等着这个结果决定几点走。",
}


def seed_exposure(runtime: Any, session: Any, name: str) -> None:
    now = datetime.now(UTC)
    text = "查到社区本周公告：周六旧书交换 16:30 结束，16:00 停止接收入场。这是本次确认的新结果。"
    event = Event(
        type="assistant_message",
        uuid="fixture-verified-result",
        ts=now,
        payload={"content": text, "partial": False},
    )
    runtime.persistent_store.append_event(session.session_id, event)
    if name in {"exposure_full", "exposure_waiting"}:
        runtime.message_exposure.store.record(
            session.session_id, event.uuid, content_version(text), "full", observed_at=now
        )
    if name == "exposure_shared":
        inner = runtime.companionship.ensure_inner()
        target = runtime.persistent_session_manager.create(
            session.initial_persona,
            session.initial_model,
            title="主动联系",
            persona_id=session.current_persona_id,
            reasoning_preset=session.initial_reasoning_preset,
        )
        delivery = runtime.session_delivery.store.submit(
            "fixture-already-shared",
            {
                "source_session_id": inner,
                "source_run_id": "prior-fixture-wake",
                "target_session_id": target.session_id,
                "destination_id": None,
                "content": text,
                "source_messages": [{"session_id": session.session_id, "message_id": event.uuid}],
            },
        )
        runtime.session_delivery.deliver_pending()
        assert (
            runtime.session_delivery.store.get(delivery["delivery_id"])["conversation_state"]
            == "committed"
        )
        # Only the isolated local outbox is exercised; no IM binding exists.
        assert (
            runtime.session_delivery.store.get(delivery["delivery_id"])["im_state"]
            == "not_requested"
        )

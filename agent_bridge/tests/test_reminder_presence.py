from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_bridge.presence import (
    DesktopPresenceSnapshot,
    DestinationPolicy,
    IMPresenceSnapshot,
    PresenceRegistry,
    PresenceState,
)

NOW = datetime(2026, 7, 28, 1, 0, tzinfo=UTC)


def _desktop(*, idle_ms: int | None, **overrides: object) -> DesktopPresenceSnapshot:
    values: dict[str, object] = {
        "observed_at": NOW,
        "current_session_id": "desktop-session",
        "window_visible": True,
        "window_focused": True,
        "window_minimized": False,
        "os_idle_ms": idle_ms,
        "session_state": "active",
        "last_desktop_interaction_at": NOW - timedelta(milliseconds=idle_ms or 0),
        "capabilities": (
            "idle",
            "lock",
            "window",
        ),
    }
    values.update(overrides)
    return DesktopPresenceSnapshot(**values)  # type: ignore[arg-type]


def _im(*, last_at: datetime | None, bind: str = "one") -> IMPresenceSnapshot:
    return IMPresenceSnapshot(
        im_type="qq",
        provider_bind_id=bind,
        chat_scope="c2c",
        chat_id=f"chat-{bind}",
        session_id=f"im-session-{bind}",
        online=True,
        last_interaction_at=last_at,
    )


def _state(
    desktop: DesktopPresenceSnapshot | None,
    *im: IMPresenceSnapshot,
    pet: bool = True,
) -> PresenceState:
    return PresenceState(desktop, tuple(im), pet, NOW)


def test_desktop_idle_boundary_29_30_31_seconds() -> None:
    policy = DestinationPolicy()
    assert policy.decide(_state(_desktop(idle_ms=29_000))).kind == "desktop"
    assert policy.decide(_state(_desktop(idle_ms=30_000))).kind == "desktop"
    assert policy.decide(_state(_desktop(idle_ms=31_000))).kind == "pet"


def test_focus_minimize_lock_stale_and_unknown_are_ineligible() -> None:
    policy = DestinationPolicy()
    cases = (
        _desktop(idle_ms=1, window_focused=False),
        _desktop(idle_ms=1, window_minimized=True),
        _desktop(idle_ms=1, session_state="locked"),
        _desktop(idle_ms=None),
        _desktop(idle_ms=1, observed_at=NOW - timedelta(seconds=16)),
    )
    assert all(policy.decide(_state(item)).kind == "pet" for item in cases)


def test_newest_real_interaction_wins_between_desktop_and_im() -> None:
    policy = DestinationPolicy()
    desktop = _desktop(idle_ms=10_000)
    assert policy.decide(_state(desktop, _im(last_at=NOW - timedelta(seconds=5)))).kind == "im"
    assert (
        policy.decide(_state(desktop, _im(last_at=NOW - timedelta(seconds=20)))).kind == "desktop"
    )


def test_single_im_without_history_is_allowed_but_multiple_are_not_random() -> None:
    policy = DestinationPolicy()
    single = policy.decide(_state(None, _im(last_at=None)))
    assert single.kind == "im"
    multiple = policy.decide(_state(None, _im(last_at=None), _im(last_at=None, bind="two")))
    assert multiple.kind == "pet"
    assert multiple.reason == "ambiguous_multiple_im_bindings"


def test_no_destination_remains_pending() -> None:
    decision = DestinationPolicy().decide(_state(None, pet=False))
    assert decision.kind == "none"
    assert decision.destination_id is None


def test_diagnostics_explain_idle_and_stale_fail_closed_reasons() -> None:
    registry = PresenceRegistry()
    registry.update_desktop(_desktop(idle_ms=31_000))
    diagnostics = registry.diagnostics(now=NOW, pet_available=True)
    assert diagnostics["desktopSignal"] == {
        "fresh": True,
        "ageMs": 0,
        "eligible": False,
        "idleBucket": "30s-2m",
        "reason": "idle_over_30s",
    }
    assert diagnostics["previewDecision"]["kind"] == "pet"  # type: ignore[index]

    stale = registry.diagnostics(now=NOW + timedelta(seconds=16), pet_available=False)
    assert stale["desktopSignal"]["reason"] == "stale_snapshot"  # type: ignore[index]
    assert stale["previewDecision"]["kind"] == "none"  # type: ignore[index]

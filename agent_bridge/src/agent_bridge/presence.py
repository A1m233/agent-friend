"""Reminder 目的地选择所需的保守桌面与 IM presence 状态。"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

DesktopSessionState = Literal["active", "locked", "sleeping", "unknown"]


@dataclass(frozen=True)
class DesktopPresenceSnapshot:
    observed_at: datetime
    current_session_id: str | None
    window_visible: bool
    window_focused: bool
    window_minimized: bool
    os_idle_ms: int | None
    session_state: DesktopSessionState
    last_desktop_interaction_at: datetime | None
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class IMPresenceSnapshot:
    im_type: str
    provider_bind_id: str
    chat_scope: str
    chat_id: str
    session_id: str
    online: bool
    last_interaction_at: datetime | None

    @property
    def destination_id(self) -> str:
        return "|".join(
            (self.im_type, self.provider_bind_id, self.chat_scope, self.chat_id, self.session_id)
        )


@dataclass(frozen=True)
class DestinationDecision:
    kind: Literal["desktop", "im", "pet", "none"]
    destination_id: str | None
    session_id: str | None
    reason: str


@dataclass(frozen=True)
class PresenceState:
    desktop: DesktopPresenceSnapshot | None
    im_bindings: tuple[IMPresenceSnapshot, ...]
    pet_available: bool
    evaluated_at: datetime


class PresenceRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._desktop: DesktopPresenceSnapshot | None = None
        self._im: dict[str, IMPresenceSnapshot] = {}
        self._last_decision: DestinationDecision | None = None

    def update_desktop(self, snapshot: DesktopPresenceSnapshot) -> None:
        with self._lock:
            if self._desktop is None or snapshot.observed_at >= self._desktop.observed_at:
                self._desktop = snapshot

    def replace_im(self, snapshots: tuple[IMPresenceSnapshot, ...]) -> None:
        with self._lock:
            self._im = {item.destination_id: item for item in snapshots}

    def snapshot(self, *, now: datetime, pet_available: bool) -> PresenceState:
        with self._lock:
            return PresenceState(
                desktop=self._desktop,
                im_bindings=tuple(self._im.values()),
                pet_available=pet_available,
                evaluated_at=now.astimezone(UTC),
            )

    def record_decision(self, decision: DestinationDecision) -> None:
        with self._lock:
            self._last_decision = decision

    def diagnostics(self, *, now: datetime, pet_available: bool) -> dict[str, object]:
        state = self.snapshot(now=now, pet_available=pet_available)
        decision = DestinationPolicy().decide(state)
        desktop = asdict(state.desktop) if state.desktop else None
        if desktop:
            for key in ("observed_at", "last_desktop_interaction_at"):
                value = desktop.get(key)
                desktop[key] = value.isoformat() if isinstance(value, datetime) else None
        return {
            "evaluatedAt": state.evaluated_at.isoformat(),
            "desktop": desktop,
            "desktopSignal": _desktop_diagnostics(state.desktop, now=state.evaluated_at),
            "im": [
                {
                    "imType": item.im_type,
                    "providerBindId": item.provider_bind_id,
                    "chatScope": item.chat_scope,
                    "chatId": item.chat_id,
                    "sessionId": item.session_id,
                    "online": item.online,
                    "lastInteractionAt": (
                        item.last_interaction_at.isoformat() if item.last_interaction_at else None
                    ),
                }
                for item in state.im_bindings
            ],
            "petAvailable": state.pet_available,
            "previewDecision": asdict(decision),
            "lastDecision": asdict(self._last_decision) if self._last_decision else None,
        }


class DestinationPolicy:
    def __init__(
        self,
        *,
        desktop_stale_after: timedelta = timedelta(seconds=15),
    ) -> None:
        self._stale_after = desktop_stale_after

    def decide(self, state: PresenceState) -> DestinationDecision:
        desktop = state.desktop
        desktop_eligible = _desktop_eligible(
            desktop,
            now=state.evaluated_at,
            stale_after=self._stale_after,
        )
        online = [item for item in state.im_bindings if item.online]
        recent_im = [item for item in online if item.last_interaction_at is not None]
        recent_im.sort(
            key=lambda item: item.last_interaction_at or datetime.min.replace(tzinfo=UTC)
        )
        newest_im = recent_im[-1] if recent_im else None

        if desktop_eligible and desktop is not None:
            desktop_at = desktop.last_desktop_interaction_at or desktop.observed_at
            newest_im_at = newest_im.last_interaction_at if newest_im is not None else None
            if newest_im_at is None or desktop_at >= newest_im_at:
                return DestinationDecision(
                    kind="desktop",
                    destination_id=desktop.current_session_id,
                    session_id=desktop.current_session_id,
                    reason="desktop_positive_evidence_newest",
                )
        if newest_im is not None:
            return DestinationDecision(
                kind="im",
                destination_id=newest_im.destination_id,
                session_id=newest_im.session_id,
                reason="recent_online_im",
            )
        if len(online) == 1:
            item = online[0]
            return DestinationDecision(
                kind="im",
                destination_id=item.destination_id,
                session_id=item.session_id,
                reason="single_online_im_without_history",
            )
        if state.pet_available:
            return DestinationDecision(
                kind="pet",
                destination_id="pet",
                session_id=None,
                reason=(
                    "ambiguous_multiple_im_bindings"
                    if len(online) > 1
                    else "desktop_and_im_unavailable"
                ),
            )
        return DestinationDecision(
            kind="none",
            destination_id=None,
            session_id=None,
            reason="no_available_destination",
        )


def _desktop_eligible(
    snapshot: DesktopPresenceSnapshot | None,
    *,
    now: datetime,
    stale_after: timedelta,
) -> bool:
    if snapshot is None or snapshot.current_session_id is None:
        return False
    if now.astimezone(UTC) - snapshot.observed_at.astimezone(UTC) > stale_after:
        return False
    if snapshot.session_state != "active":
        return False
    return snapshot.window_visible and snapshot.window_focused and not snapshot.window_minimized


def _desktop_diagnostics(
    snapshot: DesktopPresenceSnapshot | None,
    *,
    now: datetime,
) -> dict[str, object]:
    if snapshot is None:
        return {
            "fresh": False,
            "eligible": False,
            "idleBucket": "unknown",
            "reason": "missing_snapshot",
        }
    age_ms = max(
        0,
        int((now.astimezone(UTC) - snapshot.observed_at.astimezone(UTC)).total_seconds() * 1000),
    )
    fresh = age_ms <= 15_000
    idle_ms = snapshot.os_idle_ms
    idle_bucket = (
        "unknown"
        if idle_ms is None
        else "0-30s"
        if idle_ms <= 30_000
        else "30s-2m"
        if idle_ms <= 120_000
        else ">2m"
    )
    reason = "eligible"
    if not fresh:
        reason = "stale_snapshot"
    elif snapshot.current_session_id is None:
        reason = "missing_session"
    elif snapshot.session_state != "active":
        reason = f"session_{snapshot.session_state}"
    elif not snapshot.window_visible:
        reason = "window_hidden"
    elif snapshot.window_minimized:
        reason = "window_minimized"
    elif not snapshot.window_focused:
        reason = "window_unfocused"
    return {
        "fresh": fresh,
        "ageMs": age_ms,
        "eligible": reason == "eligible",
        "idleBucket": idle_bucket,
        "reason": reason,
    }


__all__ = [
    "DesktopPresenceSnapshot",
    "DestinationDecision",
    "DestinationPolicy",
    "IMPresenceSnapshot",
    "PresenceRegistry",
    "PresenceState",
]

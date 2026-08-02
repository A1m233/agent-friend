from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from agent.tools.builtin.social_platforms import (
    SocialAuthRequest,
    SocialAuthRequestConflictError,
    SocialAuthRequestStore,
    SocialPlatform,
)


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def _create(
    store: SocialAuthRequestStore,
    *,
    platform: SocialPlatform = "xiaohongshu",
    surface: str = "desktop_chat",
    destination: str | None = None,
) -> SocialAuthRequest:
    return store.create(
        session_id="session-a",
        platform=platform,
        missing_capabilities=("note_body",),
        reason="需要读取正文才能回答",
        origin_surface=surface,
        origin_destination_id=destination,
    )


def test_request_store_deduplicates_active_session_platform_and_freezes_origin() -> None:
    store = SocialAuthRequestStore()
    first = _create(
        store,
        surface="im:qq",
        destination="qq|bind|c2c|chat|session-a",
    )
    duplicate = _create(store, surface="desktop_chat")

    assert duplicate == first
    assert duplicate.origin_surface == "im:qq"
    assert duplicate.origin_destination_id == "qq|bind|c2c|chat|session-a"


def test_pending_request_expires_and_allows_a_new_request() -> None:
    clock = _Clock()
    store = SocialAuthRequestStore(ttl=timedelta(minutes=10), now_factory=clock)
    first = _create(store)

    clock.value += timedelta(minutes=10)

    assert store.get(first.request_id).status == "expired"
    replacement = _create(store)
    assert replacement.request_id != first.request_id
    assert replacement.status == "pending"


def test_transition_rejects_stale_or_repeated_actions() -> None:
    store = SocialAuthRequestStore()
    request = _create(store)

    approved = store.transition(
        request.request_id,
        expected={"pending"},
        status="login_in_progress",
    )
    assert approved.status == "login_in_progress"

    with pytest.raises(SocialAuthRequestConflictError):
        store.transition(
            request.request_id,
            expected={"pending"},
            status="declined",
        )


def test_close_cancels_every_active_request_without_rewriting_terminal_state() -> None:
    store = SocialAuthRequestStore()
    active = _create(store)
    terminal = store.create(
        session_id="session-b",
        platform="xiaohongshu",
        missing_capabilities=("note_body",),
        reason="需要正文",
        origin_surface="desktop_chat",
        origin_destination_id=None,
    )
    store.transition(terminal.request_id, expected={"pending"}, status="declined")

    store.close()

    assert store.get(active.request_id).status == "cancelled"
    assert store.get(terminal.request_id).status == "declined"

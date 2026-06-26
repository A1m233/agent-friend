"""``calls/registry.py`` 单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from voice_bridge.calls import CallBinding, CallRegistry


def _make_binding(call_id: str = "c1", session_id: str = "s1") -> CallBinding:
    return CallBinding(
        call_id=call_id,
        session_id=session_id,
        state="active",
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        room_id="room-1",
        bot_user_id="bot-1",
        target_user_id="user-1",
    )


class TestCallRegistry:
    def test_bind_and_lookup(self) -> None:
        reg = CallRegistry()
        binding = _make_binding()
        reg.bind(binding)
        assert reg.lookup("c1") is binding

    def test_lookup_missing_returns_none(self) -> None:
        reg = CallRegistry()
        assert reg.lookup("nonexistent") is None

    def test_bind_duplicate_raises(self) -> None:
        reg = CallRegistry()
        reg.bind(_make_binding())
        with pytest.raises(KeyError, match="重复"):
            reg.bind(_make_binding())

    def test_update_state_returns_new_binding(self) -> None:
        reg = CallRegistry()
        reg.bind(_make_binding())
        updated = reg.update_state("c1", "stopped")
        assert updated.state == "stopped"
        assert updated.call_id == "c1"
        looked = reg.lookup("c1")
        assert looked is not None
        assert looked.state == "stopped"

    def test_update_state_missing_raises(self) -> None:
        reg = CallRegistry()
        with pytest.raises(KeyError):
            reg.update_state("missing", "stopped")

    def test_next_round_increments_round_seq(self) -> None:
        reg = CallRegistry()
        reg.bind(_make_binding())
        first = reg.next_round("c1")
        second = reg.next_round("c1")
        assert first.round_seq == 1
        assert second.round_seq == 2
        assert reg.lookup("c1") == second

    def test_unbind_returns_removed(self) -> None:
        reg = CallRegistry()
        binding = _make_binding()
        reg.bind(binding)
        removed = reg.unbind("c1")
        assert removed == binding
        assert reg.lookup("c1") is None

    def test_unbind_missing_returns_none(self) -> None:
        reg = CallRegistry()
        assert reg.unbind("nonexistent") is None

    def test_list_active_only(self) -> None:
        reg = CallRegistry()
        reg.bind(_make_binding(call_id="active-1"))
        reg.bind(_make_binding(call_id="stop-1"))
        reg.update_state("stop-1", "stopped")
        actives = reg.list_active()
        assert len(actives) == 1
        assert actives[0].call_id == "active-1"

    def test_now_returns_utc(self) -> None:
        ts = CallRegistry.now()
        assert ts.tzinfo is not None

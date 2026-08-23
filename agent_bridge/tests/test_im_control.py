from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.control import MENU_PAYLOAD, IMControlService
from agent_bridge.protocols.im.coordinator import IMBindingCoordinator
from agent_bridge.protocols.im.event import InboundInteraction
from agent_bridge.protocols.im.provider import IMProvider, ProviderStatus
from agent_bridge.protocols.im.state import ControlCapability, IMBindingKey, IMStateStore
from agent_bridge.session_models import SessionModelService

from agent import SessionManager


class FakeProvider:
    type = "qq"
    bind_id = "OPENID-USER"

    def __init__(self) -> None:
        self.sent: list[OutboundContent] = []

    def start(self, on_inbound: Any, on_interaction: Any = None) -> None:
        pass

    async def send(self, content: OutboundContent) -> None:
        self.sent.append(content)

    async def stop(self) -> None:
        pass

    def status(self) -> ProviderStatus:
        return "active"

    def controls_capability(self) -> ControlCapability:
        return "supported"


class FakeSessions:
    def __init__(self) -> None:
        self.sessions = {
            "SESSION-A": SimpleNamespace(
                session_id="SESSION-A",
                initial_title="Alpha",
                current_model="model-a",
                messages=[],
            ),
            "SESSION-B": SimpleNamespace(
                session_id="SESSION-B",
                initial_title="Beta",
                current_model="model-b",
                messages=[SimpleNamespace(role="assistant", content="最近一条回复")],
            ),
        }
        now = datetime.now(UTC)
        self.summaries = [
            SimpleNamespace(session_id="SESSION-A", title="Alpha", updated_at=now),
            SimpleNamespace(session_id="SESSION-B", title="Beta", updated_at=now),
        ]

    def open(self, session_id: str) -> Any:
        return self.sessions[session_id]

    def list(self, *, archived: bool = False) -> list[Any]:
        return [] if archived else self.summaries


class FakeModels:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def switch(self, session_id: str, model_id: str) -> str:
        self.calls.append((session_id, model_id))
        return model_id


def interaction(payload: str) -> InboundInteraction:
    return InboundInteraction(
        interaction_id="INTERACTION-1",
        event_type="INTERACTION_CREATE",
        chat_scope="c2c",
        chat_id="OPENID-USER",
        operator_id="OPENID-USER",
        payload=payload,
    )


def make_service(
    tmp_path: Path,
) -> tuple[
    IMControlService,
    FakeProvider,
    IMStateStore,
    IMBindingCoordinator,
    FakeModels,
]:
    sessions = FakeSessions()
    state = IMStateStore(tmp_path / "im_state.json")
    state.set_binding(
        IMBindingKey("qq", "OPENID-USER", "c2c", "OPENID-USER"),
        "SESSION-A",
    )
    coordinator = IMBindingCoordinator()
    models = FakeModels()
    service = IMControlService(
        sessions=cast(SessionManager, sessions),
        state_store=state,
        coordinator=coordinator,
        model_service=cast(SessionModelService, models),
    )
    return service, FakeProvider(), state, coordinator, models


@pytest.mark.asyncio
async def test_menu_switches_future_session_without_routing_control_to_agent(
    tmp_path: Path,
) -> None:
    service, provider, state, _coordinator, _models = make_service(tmp_path)

    await service.handle(cast(IMProvider, provider), interaction(MENU_PAYLOAD))
    menu = provider.sent[-1]
    assert menu.text == "当前会话：Alpha\n当前模型：model-a"
    assert menu.text_format == "markdown"
    assert menu.controls is not None
    assert menu.controls.rows[0][0].click_limit is None
    session_menu_payload = menu.controls.rows[0][0].payload

    await service.handle(cast(IMProvider, provider), interaction(session_menu_payload))
    session_page = provider.sent[-1]
    assert session_page.controls is not None
    beta_payload = next(
        button.payload
        for row in session_page.controls.rows
        for button in row
        if "Beta" in button.label
    )
    await service.handle(cast(IMProvider, provider), interaction(beta_payload))

    key = IMBindingKey("qq", "OPENID-USER", "c2c", "OPENID-USER")
    assert state.get_binding(key).session_id == "SESSION-B"  # type: ignore[union-attr]
    assert provider.sent[-1].controls is None
    assert provider.sent[-1].text_format == "plain_text"
    assert "最近回复：最近一条回复" in provider.sent[-1].text


@pytest.mark.asyncio
async def test_dynamic_menu_button_can_be_reused_within_token_ttl(tmp_path: Path) -> None:
    service, provider, _state, _coordinator, _models = make_service(tmp_path)

    await service.handle(cast(IMProvider, provider), interaction(MENU_PAYLOAD))
    menu = provider.sent[-1]
    assert menu.controls is not None
    session_menu_payload = menu.controls.rows[0][0].payload

    await service.handle(cast(IMProvider, provider), interaction(session_menu_payload))
    first_page = provider.sent[-1]
    await service.handle(cast(IMProvider, provider), interaction(session_menu_payload))
    second_page = provider.sent[-1]

    assert first_page.text == second_page.text == "选择会话（第 1/1 页）"
    assert second_page.controls is not None


@pytest.mark.asyncio
async def test_session_pagination_keeps_ids_unique_for_same_prefixes(tmp_path: Path) -> None:
    service, provider, _state, _coordinator, _models = make_service(tmp_path)
    sessions = cast(Any, service._sessions)
    now = datetime.now(UTC)
    sessions.summaries = [
        SimpleNamespace(session_id=f"ordinary-{index}", title=f"Ordinary {index}", updated_at=now)
        for index in range(8)
    ] + [
        SimpleNamespace(
            session_id=f"acceptance-059-{kind}-{index}",
            title=f"Acceptance {index}",
            updated_at=now,
        )
        for index, kind in enumerate(("image", "pdf", "txt", "image-extra"))
    ]

    await service.handle(cast(IMProvider, provider), interaction(MENU_PAYLOAD))
    menu = provider.sent[-1]
    assert menu.controls is not None
    await service.handle(cast(IMProvider, provider), interaction(menu.controls.rows[0][0].payload))
    first_page = provider.sent[-1]
    assert first_page.controls is not None
    next_payload = next(
        action.payload
        for row in first_page.controls.rows
        for action in row
        if action.label == "下一页"
    )
    await service.handle(cast(IMProvider, provider), interaction(next_payload))
    second_page = provider.sent[-1]
    assert second_page.controls is not None
    next_payload = next(
        action.payload
        for row in second_page.controls.rows
        for action in row
        if action.label == "下一页"
    )
    await service.handle(cast(IMProvider, provider), interaction(next_payload))

    third_page = provider.sent[-1]
    assert third_page.text == "选择会话（第 3/3 页）"
    assert third_page.controls is not None
    ids = [action.id for row in third_page.controls.rows for action in row]
    assert len(ids) == len(set(ids))
    assert all(":" not in action_id for action_id in ids)


@pytest.mark.asyncio
async def test_session_switch_is_rejected_while_im_reply_is_running(tmp_path: Path) -> None:
    service, provider, state, coordinator, _models = make_service(tmp_path)
    await service.handle(cast(IMProvider, provider), interaction(MENU_PAYLOAD))
    menu = provider.sent[-1]
    assert menu.controls is not None
    await service.handle(cast(IMProvider, provider), interaction(menu.controls.rows[0][0].payload))
    page = provider.sent[-1]
    assert page.controls is not None
    beta_payload = next(
        button.payload for row in page.controls.rows for button in row if "Beta" in button.label
    )
    key = IMBindingKey("qq", "OPENID-USER", "c2c", "OPENID-USER")

    async with coordinator.turn(key):
        await service.handle(cast(IMProvider, provider), interaction(beta_payload))

    assert state.get_binding(key).session_id == "SESSION-A"  # type: ignore[union-attr]
    assert provider.sent[-1].text == "请等待当前回复完成后再切换会话。"


@pytest.mark.asyncio
async def test_menu_switches_model_for_bound_session(tmp_path: Path) -> None:
    service, provider, _state, _coordinator, models = make_service(tmp_path)
    await service.handle(cast(IMProvider, provider), interaction(MENU_PAYLOAD))
    menu = provider.sent[-1]
    assert menu.controls is not None
    await service.handle(cast(IMProvider, provider), interaction(menu.controls.rows[0][1].payload))
    page = provider.sent[-1]
    assert page.controls is not None
    model_payload = page.controls.rows[0][0].payload

    await service.handle(cast(IMProvider, provider), interaction(model_payload))

    assert len(models.calls) == 1
    assert models.calls[0][0] == "SESSION-A"
    assert provider.sent[-1].text.startswith("模型已切换为 ")
    assert provider.sent[-1].controls is None


def test_state_snapshot_round_trip_preserves_binding_and_capability(tmp_path: Path) -> None:
    key = IMBindingKey("qq", "OPENID-USER", "c2c", "OPENID-USER")
    source = IMStateStore(tmp_path / "source.json")
    source.set_binding(key, "SESSION-A")
    interaction_at = datetime(2026, 8, 23, 1, 2, tzinfo=UTC)
    source.record_interaction(key, interaction_at)
    source.set_capability("qq", "OPENID-USER", "supported")

    restored = IMStateStore(tmp_path / "restored.json")
    restored.replace(source.snapshot())

    binding = restored.get_binding(key)
    assert binding is not None
    assert binding.session_id == "SESSION-A"
    assert binding.last_interaction_at == interaction_at
    assert restored.capability("qq", "OPENID-USER").status == "supported"

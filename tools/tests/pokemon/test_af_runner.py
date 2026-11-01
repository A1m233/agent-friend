from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from llm_providers.catalog import DEEPSEEK_VISION_CAPABILITIES
from llm_providers.multimodal.tool_images import supports_tool_images
from PIL import Image
from tools.pokemon.budget import BudgetModel, PlayBudget, PlayStopped
from tools.pokemon.runner import play
from tools.pokemon.runtime.client import GameClient
from tools.pokemon.runtime.protocol import GameError, atomic_json
from tools.pokemon.setup import install, prepare

from agent import JsonlSessionStore
from llm_providers import LLMClient, LLMToolCallDelta, LLMTurnDone, ModelRequest, ProviderSpec


class FakeModel(LLMClient):
    def __init__(self) -> None:
        super().__init__(
            ProviderSpec(
                model="openai/test",
                api_key="unused-test-only",
                context_window=128000,
                attachment_capabilities=DEEPSEEK_VISION_CAPABILITIES,
            )
        )
        self.requests: list[ModelRequest] = []

    def generate(self, request: Any) -> Any:
        raise AssertionError("Fake test must never fall through to a provider call")

    def stream(self, request: Any, **kwargs: Any) -> Any:
        assert isinstance(request, ModelRequest)
        index = len(self.requests)
        self.requests.append(request)
        if index == 0:
            name, args = "pokemon_observe", {}
        elif index == 1:
            name, args = "pokemon_notes", {"text": "从测试画面看见红色背景。"}
        elif index == 2:
            yield LLMTurnDone(stop_reason="end_turn")
            return
        else:
            name, args = (
                "pokemon_finish",
                {"reason": "needs_attention", "detail": "fake test complete"},
            )
        yield LLMToolCallDelta(
            tool_call_id=f"call-{index}", tool_name=name, args_json_delta=json.dumps(args)
        )
        yield LLMTurnDone(stop_reason="tool_use")


def budget_at(root: Path, calls: int = 2) -> PlayBudget:
    atomic_json(
        root / "play.json", {"max_calls": calls, "calls_reserved": 0, "deadline": time.time() + 30}
    )
    return PlayBudget(root)


def test_budget_survives_restart_and_preserves_image_route(tmp_path: Path) -> None:
    model = FakeModel()
    budget = budget_at(tmp_path, 1)
    wrapper = BudgetModel(model, budget)
    assert supports_tool_images(wrapper)
    list(wrapper.stream(ModelRequest(messages=[])))
    control = model.requests[0].control
    assert not control.allow_retries and control.retry_limit == 0
    assert control.deadline_monotonic is not None
    with pytest.raises(PlayStopped):
        list(BudgetModel(model, PlayBudget(tmp_path)).stream(ModelRequest(messages=[])))
    assert len(model.requests) == 1


def test_binding_does_not_bypass_budget(tmp_path: Path) -> None:
    wrapper = BudgetModel(FakeModel(), budget_at(tmp_path, 1))
    with wrapper.bind_execution() as bound:
        list(bound.stream(ModelRequest(messages=[])))
    with wrapper.bind_execution() as bound, pytest.raises(PlayStopped):
        list(bound.stream(ModelRequest(messages=[])))


def test_pause_prevents_provider_request(tmp_path: Path) -> None:
    model = FakeModel()
    budget = budget_at(tmp_path)
    (tmp_path / "paused").touch()
    with pytest.raises(PlayStopped):
        list(BudgetModel(model, budget).stream(ModelRequest(messages=[])))
    assert not model.requests


def test_no_real_model_without_explicit_flag(tmp_path: Path) -> None:
    with pytest.raises(GameError) as error:
        play(
            tmp_path,
            tmp_path,
            model="unused",
            persona="default",
            max_calls=1,
            max_seconds=1,
            allow_llm=False,
        )
    assert error.value.code == "llm_not_authorized"
    assert not (tmp_path / "play.json").exists()


def test_af_scheduler_persists_images_continues_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rom, emulator = tmp_path / "test.gba", tmp_path / "emulator"
    rom.write_bytes(bytes(256))
    emulator.touch()
    install(tmp_path / "skills")
    skill = tmp_path / "skills/pokemon-firered"
    prepared = prepare(
        rom=rom,
        emulator=emulator,
        instances_root=tmp_path / "instances",
        skill_path=skill,
        source="fake",
        language="homebrew-test",
    )
    root = Path(str(prepared["instance_root"]))
    screenshot = root / "fake.png"
    Image.new("RGB", (240, 160), "red").save(screenshot)
    save = root / "saves" / ("a" * 32 + ".ss0")
    save.write_bytes(b"fake save")

    def request(self: GameClient, kind: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "completed",
            "observation_id": "a" * 32,
            "operation_id": "a" * 32,
            "screenshot": str(screenshot),
            "save_path": str(save),
            "emulator_boot": "boot1",
        }

    monkeypatch.setattr(GameClient, "request", request)
    model = FakeModel()
    result = play(
        root,
        skill,
        model="deepseek/deepseek-chat",
        persona="default",
        max_calls=8,
        max_seconds=30,
        allow_llm=True,
        model_factory=lambda _config: model,
        session_root=tmp_path / "sessions",
        asset_root=tmp_path / "assets",
    )
    assert result["status"] == "paused"
    assert result["turns"] == 2
    assert len(model.requests) == 4
    assert list((root / "checkpoints").glob("*/manifest.json"))
    assert (root / "paused").exists()
    assert (
        json.loads((root / "finish.json").read_text(encoding="utf-8"))["reason"]
        == "needs_attention"
    )
    assert any("image_url" in json.dumps(msg) for msg in model.requests[1].messages)
    names = {tool["function"]["name"] for tool in model.requests[0].tools or []}
    assert names == {"pokemon_observe", "pokemon_act", "pokemon_notes", "pokemon_finish"}
    saved = JsonlSessionStore(tmp_path / "sessions").load(result["session_id"])
    triggers = [e for e in saved.events if e.type == "system_trigger"]
    assert len(triggers) == 2
    assert all(e.payload["source_kind"] == "pokemon_autoplay" for e in triggers)
    assert not any(e.type == "user_message" for e in saved.events)
    assert any(e.type == "tool_call_result" and e.payload.get("images") for e in saved.events)
    from tools.pokemon.recovery import recover

    checkpoint = next(
        p
        for p in (root / "checkpoints").iterdir()
        if len(
            [
                e
                for e in json.loads((p / "session.json").read_text(encoding="utf-8"))["events"]
                if e["type"] == "system_trigger"
            ]
        )
        == 1
    )
    recovery = recover(
        root, checkpoint.name, "test a host crash", session_root=tmp_path / "sessions"
    )
    assert recovery["previous_session_id"] == result["session_id"]
    branch = JsonlSessionStore(tmp_path / "sessions").load(recovery["new_session_id"])
    assert branch.session_id != saved.session_id
    assert branch.origin_kind == "pokemon_recovery"
    assert len([e for e in branch.events if e.type == "system_trigger"]) == 1
    assert len([e for e in saved.events if e.type == "system_trigger"]) == 2
    after = json.loads((root / "play.json").read_text(encoding="utf-8"))
    assert after["calls_reserved"] == result["calls_reserved"]
    assert after["deadline"] == result["deadline"]
    assert not (root / "recovery_pending.json").exists()

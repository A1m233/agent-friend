"""Real companionship guard and image pipeline; provider I/O is scripted offline."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agent.attachments import AttachmentStore
from agent.companionship.model import GovernedModel
from agent.conversation_events import AssistantImage, TurnDone
from agent.filesystem import FilesystemService
from agent.image_reading import ImageReadingService
from agent.messages import Message
from agent.session_files import SessionFileService
from agent.tools.builtin.image_gen.tool import ImagegenTool
from agent.tools.builtin.view_image import ViewImageTool
from agent.tools.registry import ToolRegistry
from llm_providers.connections.directory import custom_tool_image_capabilities
from llm_providers.errors import LLMAuthError, LLMImageInputUnsupportedError
from llm_providers.multimodal.tool_images import record_image_rejection, supports_tool_images
from llm_providers.responses.request import convert_messages
from llm_providers.spec import ImagesRoute, ResponsesRoute

from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    MultimodalInputAdapter,
    ProviderAssistantTurn,
    ProviderSpec,
    ProviderToolCall,
)

from .test_assistant_phase import _make_conversation
from .test_command_image_output import setup as command_images
from .test_generated_images import png


def client(*, supported: bool = True, chat: bool = False) -> LLMClient:
    return LLMClient(
        ProviderSpec(
            "openai/fixture",
            "fixture",
            context_window=128000,
            attachment_capabilities=replace(
                custom_tool_image_capabilities(), multimodal_tool_results=supported
            ),
            responses=None if chat else ResponsesRoute("fixture", "https://unused.invalid"),
            images=ImagesRoute("https://unused.invalid", None),
        )
    )


def guard(model: Any, events: list[str]) -> GovernedModel:
    return GovernedModel(
        model,
        record_call=lambda: events.append("call"),
        check=lambda: events.append("check"),
        submitted=lambda: events.append("submitted"),
        usage=lambda _: events.append("usage"),
    )


@pytest.mark.parametrize("depth", [1, 2])
def test_guard_preserves_capability_and_rejection_is_shared(depth: int) -> None:
    base, other = client(), client()
    wrapped: Any = base
    for _ in range(depth):
        wrapped = guard(wrapped, [])
    assert supports_tool_images(wrapped)
    assert not record_image_rejection(wrapped, LLMAuthError("not an image rejection"))
    assert supports_tool_images(base)
    error = LLMImageInputUnsupportedError("image_input_unsupported")
    assert record_image_rejection(wrapped, error)
    assert not supports_tool_images(base)
    assert not supports_tool_images(guard(base, []))
    assert not record_image_rejection(base, error)
    assert supports_tool_images(other)


@pytest.mark.parametrize("reason", ["text_only", "rejected", "unknown", "route"])
def test_unsupported_guard_never_reads_image_assets(tmp_path: Path, reason: str) -> None:
    model = client(supported=reason != "text_only")
    if reason == "rejected":
        assert record_image_rejection(model, LLMImageInputUnsupportedError("unsupported"))
    elif reason == "unknown":
        # A lookalike spec without the explicit optional contract is not enough.
        model = type("UnknownModel", (), {"spec": model.spec})()
    elif reason == "route":
        model.spec = replace(
            model.spec, responses=ResponsesRoute("fixture", "https://unused.invalid", "kimi")
        )
    wrapped = guard(model, [])
    assets = AttachmentStore(tmp_path / "assets")
    _, image = assets.import_image_bytes(png(), display_name="fixture.png")
    adapter = MultimodalInputAdapter(open_asset=lambda _: pytest.fail("must not read image"))
    message = Message(role="tool", content="snapshot", images=(image,))
    assert not supports_tool_images(wrapped)
    rendered = adapter.render_message(message, wrapped)
    assert "未提供" in rendered["content"]
    assert adapter.render_budget_message(message, wrapped) == rendered


def test_guarded_command_image_output_retains_scope_checks(tmp_path: Path) -> None:
    output, context, scope = command_images(tmp_path)
    from agent.tools.protocol import IMAGE_INPUT_SUPPORTED_ARG

    args = {
        "output_images": {"filesystem_scope_id": scope},
        IMAGE_INPUT_SUPPORTED_ARG: supports_tool_images(guard(client(), [])),
    }
    assert output.validate(args, context) == scope
    assert output.collect({"stdout": '{"image_paths":["frame.png"]}'}, context, scope)
    with pytest.raises(ValueError, match="read_image"):
        output.validate({**args, "output_images": {"filesystem_scope_id": "expired"}}, context)


def test_image_capability_does_not_bypass_guard_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    model = client()
    monkeypatch.setattr(model, "stream", lambda _: pytest.fail("must not call provider"))
    monkeypatch.setattr(model, "generate", lambda _: pytest.fail("must not call provider"))
    events: list[str] = []
    wrapped = guard(model, events)

    def stop() -> None:
        raise RuntimeError("activity stopped or quota exceeded")

    wrapped.check = stop
    assert supports_tool_images(wrapped)
    for invoke in (
        lambda: list(wrapped.stream(ModelRequest([]))),
        lambda: wrapped.generate(ModelRequest([])),
    ):
        with pytest.raises(RuntimeError, match="activity stopped"):
            invoke()
    assert events == []


@pytest.mark.parametrize("chat", [False, True])
def test_guarded_generate_then_view_sends_image_and_keeps_governance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chat: bool
) -> None:
    model = client(chat=chat)
    conv = _make_conversation(tmp_path, fake_llm=model)  # type: ignore[arg-type]
    assets = AttachmentStore(tmp_path / "assets")
    files = SessionFileService(conv._store, assets, tmp_path / "workspaces")
    adapter = MultimodalInputAdapter(open_asset=assets.open)
    conv._message_renderer = adapter.render_message
    conv._message_budget_renderer = adapter.render_budget_message
    conv._generated_image_store = assets
    conv._tool_registry = ToolRegistry(
        [
            ImagegenTool(files, FilesystemService()),
            ViewImageTool(FilesystemService(), ImageReadingService(assets), files),
        ]
    )
    events: list[str] = []
    conv.model_guard = lambda bound: guard(bound, events)
    requests: list[ModelRequest] = []
    image_bytes = png()
    monkeypatch.setattr("agent.tools.builtin.image_gen.tool.request_image", lambda *_: image_bytes)

    def stream(request: ModelRequest) -> Iterator[LLMStreamEvent]:
        requests.append(request)
        assert not request.control.allow_retries and request.control.retry_limit == 0
        assert request.control.cancel_check is not None
        before = events.count("check")
        request.control.cancel_check()
        assert events.count("check") > before
        names = {tool["function"]["name"] for tool in request.tools or []}
        assert {"view_image", "imagegen"} <= names
        if len(requests) == 1:
            name, args = "imagegen", {"prompt": "draw a red square"}
        elif len(requests) == 2:
            (ref,) = files.files(conv.session.session_id)
            assert ref.file_ref in str(request.messages)
            name, args = "view_image", {"file_ref": ref.file_ref}
        else:
            assert len(requests) == 3
            image_messages = [
                m
                for m in request.messages
                if isinstance(m.get("content"), list)
                and any(b.get("type") == "image_url" for b in m["content"])
            ]
            assert len(image_messages) == 1
            image_message = image_messages[0]
            blocks = image_message["content"]
            url = next(b["image_url"]["url"] for b in blocks if b["type"] == "image_url")
            assert base64.b64decode(url.split(",", 1)[1]) == image_bytes
            assert image_message["role"] == ("user" if chat else "tool")
            if not chat:
                wire = convert_messages(request.messages)
                assert any(
                    m.get("type") == "function_call_output"
                    and isinstance(m.get("output"), list)
                    and any(b.get("type") == "input_image" for b in m["output"])
                    for m in wire
                )
            yield LLMTextDelta("fixture: image received")
            yield LLMTurnDone("end_turn")
            return
        call_id = f"image-{len(requests)}"
        arguments = json.dumps(args)
        yield LLMToolCallDelta(0, call_id, name, arguments)
        yield LLMTurnDone(
            "tool_use",
            assistant_turn=ProviderAssistantTurn(
                None, (ProviderToolCall(0, call_id, name, arguments, 0),)
            ),
        )

    monkeypatch.setattr(model, "stream", stream)
    result = list(conv.stream("generate and inspect", run_id="guarded-images"))
    assert len(requests) == 3
    assert events.count("call") == events.count("usage") == events.count("submitted") == 3
    assert sum(isinstance(e, AssistantImage) for e in result) == 1
    assert [e.stop_reason for e in result if isinstance(e, TurnDone)] == ["end_turn"]

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path

import pytest
from agent.context import SummarizingContextManager
from agent.filesystem.service import FilesystemService
from agent.image_reading import ImageReadingService
from agent.session_files import SessionFileService
from agent.session_files.inputs import SessionFileInputs
from agent.tools import ToolRegistry
from agent.tools.builtin.view_image.tool import ViewImageTool
from agent.tools.protocol import IMAGE_INPUT_SUPPORTED_ARG
from PIL import Image

from agent import AttachmentStore, Conversation, Message

from .test_context_summarizing import _make_conversation
from .test_context_usage_anchor import MeasuredLLM
from .test_session_files import (
    SESSION_ARG,
    Environment,
    chunks,
    upload,
)


@pytest.fixture
def environment(tmp_path: Path) -> Environment:
    from agent.sessions import JsonlSessionStore, Session
    from agent.tools.builtin.session_files import SessionFilesTool

    sessions = JsonlSessionStore(tmp_path / "sessions")
    for sid in ("one", "two"):
        sessions.create(Session.new(title="test", persona="default", model="fake", session_id=sid))
    assets = AttachmentStore(tmp_path / "assets")
    service = SessionFileService(sessions, assets, tmp_path / "workspaces")
    return sessions, assets, service, ToolRegistry([SessionFilesTool(service)])


def test_send_prepares_before_model_request_and_projection_is_pure(tmp_path: Path) -> None:
    assets = AttachmentStore(tmp_path / "assets")
    _, ref = asyncio.run(
        assets.import_stream(chunks(b"private binary\x00"), display_name="input.zip")
    )
    llm = MeasuredLLM(context_window=100000, prompt_tokens=1000)
    original, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    service = SessionFileService(store, assets, tmp_path / "workspace")
    conv = Conversation(
        session=session,
        store=store,
        llm_client=llm,
        prompt_builder=original._prompt_builder,
        context_manager=SummarizingContextManager(),
        session_file_service=service,
    )
    list(conv.stream("read", attachments=[ref]))
    files = service.files(session.session_id)
    assert len(files) == 1
    prepared = service.prepare(session.session_id, files[0].file_ref)
    assert (
        Path(prepared["workspace"]) / prepared["relative_path"]
    ).read_bytes() == b"private binary\x00"
    request = json.dumps(llm.received[0], ensure_ascii=False)
    assert files[0].file_ref in request and prepared["relative_path"] in request
    assert "private binary" not in request
    assert store.load(session.session_id).messages[0].attachments == [ref]


def test_failed_preparation_preserves_accepted_input_without_calling_model(tmp_path: Path) -> None:
    assets = AttachmentStore(tmp_path / "assets")
    _, ref = asyncio.run(assets.import_stream(chunks(b"body"), display_name="input.dat"))
    llm = MeasuredLLM(context_window=100000)
    original, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    assets.verify(ref.asset_id).unlink()
    conv = Conversation(
        session=session,
        store=store,
        llm_client=llm,
        prompt_builder=original._prompt_builder,
        context_manager=SummarizingContextManager(),
        session_file_service=SessionFileService(store, assets, tmp_path / "workspace"),
    )
    with pytest.raises(ValueError, match="原件缺失或校验失败"):
        list(conv.stream("read", attachments=[ref]))
    assert not llm.received
    assert any(
        event.type == "user_message" and event.payload["attachments"]
        for event in store.load(session.session_id).active_events
    )


def test_projection_does_not_prepare_and_failed_prepare_does_not_reuse_state(
    environment: Environment,
) -> None:
    sessions, assets, service, _ = environment
    event = upload(sessions, assets, "one", b"binary\x00", "same.bin")
    message = sessions.load("one").messages[-1]
    binding = SessionFileInputs(service, "one")
    assert "relative_path" not in binding.project(message).content
    assert not service.root.exists()
    binding.prepare(event.uuid)
    assert "relative_path" in binding.project(message).content
    assets.verify(message.attachments[0].asset_id).unlink()
    with pytest.raises(ValueError):
        binding.prepare(event.uuid)
    assert "relative_path" not in binding.project(message).content
    # A new message with different provenance must not acquire a prepared reference.
    assert (
        "relative_path"
        not in binding.project(
            Message(role="user", content="", attachments=message.attachments)
        ).content
    )


def test_uploaded_image_reference_is_scoped_and_does_not_grant_directory_access(
    environment: Environment,
) -> None:
    sessions, assets, service, _ = environment
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    upload(sessions, assets, "one", buffer.getvalue(), "photo.png")
    ref = service.files("one")[0].file_ref
    registry = ToolRegistry(
        [ViewImageTool(FilesystemService(), ImageReadingService(assets), service)]
    )
    args = {"file_ref": ref, SESSION_ARG: "one", IMAGE_INPUT_SUPPORTED_ARG: True}
    result = registry.invoke("view_image", args)
    assert not result.is_error and len(result.images) == 1
    denied = registry.invoke("view_image", {**args, SESSION_ARG: "two"})
    assert denied.is_error and "session_file_not_found" in denied.text
    assert registry.invoke("view_image", {**args, "path": "."}).is_error
    assert not service.root.exists()


def test_image_rejection_rebuilds_without_images_and_preserves_tool_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Iterator
    from typing import Any

    from llm_providers.catalog import DEEPSEEK_VISION_CAPABILITIES
    from llm_providers.errors import LLMImageInputUnsupportedError

    from llm_providers import LLMClient, LLMTextDelta, LLMTurnDone, ModelRequest, ProviderSpec

    client = LLMClient(
        ProviderSpec(
            model="openai/fixture",
            api_key="fixture",
            context_window=100000,
            attachment_capabilities=DEEPSEEK_VISION_CAPABILITIES,
        )
    )
    received = []

    def stream(request: ModelRequest) -> Iterator[Any]:
        received.append(request.messages)
        if len(received) == 1:
            raise LLMImageInputUnsupportedError("Model call failed: image_input_unsupported")
        yield LLMTextDelta(text="image unavailable")
        yield LLMTurnDone(stop_reason="end_turn")

    monkeypatch.setattr(client, "stream", stream)
    conv, _, _ = _make_conversation(
        tmp_path, fake_llm=client, context_manager=SummarizingContextManager()
    )
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}], "content": ""},
        {
            "role": "tool",
            "tool_call_id": "a",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}],
        },
    ]
    recovered = [{"role": "tool", "tool_call_id": "a", "content": "工具图像未提供"}]
    list(conv._iter_model_stream(messages, tools=None, recovery_builder=lambda: recovered))
    assert len(received) == 2 and received[-1] == recovered
    assert client.tool_images_rejected

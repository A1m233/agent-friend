from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent.attachments import AttachmentStore
from agent.conversation_events import AssistantImage, TurnDone
from agent.filesystem import FilesystemAuthorizationLedger, FilesystemService
from agent.filesystem.tools import (
    FILESYSTEM_LEDGER_ARG,
    FILESYSTEM_SESSION_ID_ARG,
    FILESYSTEM_TOOL_CALL_ID_ARG,
)
from agent.session_files import SessionFileService
from agent.tool_runtime.control import (
    TOOL_INVOCATION_CONTROL_ARG,
    ToolInvocationCancelled,
    ToolInvocationControl,
)
from agent.tools.builtin.image_gen.tool import IMAGE_SPEC_ARG, ImagegenTool
from agent.tools.registry import ToolRegistry
from llm_providers.errors import LLMEmptyResponseError
from llm_providers.image_output import LLMImageResult, LLMImageStart
from llm_providers.spec import ImagesRoute, ProviderSpec
from PIL import Image

from llm_providers import (
    LLMStreamEvent,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ProviderAssistantTurn,
    ProviderToolCall,
)

from .test_assistant_phase import NativeModel, _make_conversation, items
from .test_filesystem_service import _grant, _policy
from .test_generated_images import png

SPEC = ProviderSpec(
    "openai/fixture", "fixture-key", images=ImagesRoute("https://selected.invalid/v1", None)
)


def test_tool_availability_follows_the_active_route(tmp_path: Path) -> None:
    class SelectedModel(NativeModel):
        spec = SPEC

    model = SelectedModel()
    conv = _make_conversation(tmp_path, fake_llm=model)
    files = SessionFileService(conv._store, AttachmentStore(tmp_path / "assets"), tmp_path)
    conv._tool_registry = ToolRegistry([ImagegenTool(files, FilesystemService())])
    assert [s["function"]["name"] for s in conv._current_tool_specs() or []] == ["imagegen"]
    model.spec = ProviderSpec("other/chat", "other-key")
    assert conv._current_tool_specs() == []


@pytest.mark.parametrize("native_first", [False, True])
def test_same_turn_duplicate_produces_one_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_first: bool
) -> None:
    class ImageModel(NativeModel):
        spec = SPEC

    args = json.dumps({"prompt": "draw"})

    def call(call_id: str) -> list[LLMStreamEvent]:
        return [
            LLMToolCallDelta(0, call_id, "imagegen", args),
            LLMTurnDone(
                "tool_use",
                assistant_turn=ProviderAssistantTurn(
                    None, (ProviderToolCall(0, call_id, "imagegen", args, 1),)
                ),
            ),
        ]

    first = call("one")
    if native_first:
        first = [LLMImageStart("native", 0), LLMImageResult("native", 0, png()), *first]
    script = [first, items()] if native_first else [first, call("two"), items()]
    conv = _make_conversation(tmp_path, fake_llm=ImageModel(script=script))
    assets = AttachmentStore(tmp_path / "assets")
    conv._generated_image_store = assets
    files = SessionFileService(conv._store, assets, tmp_path)
    conv._tool_registry = ToolRegistry([ImagegenTool(files, FilesystemService())])
    calls = []

    def respond(*req: Any) -> bytes:
        calls.append(req[3])
        return png()

    monkeypatch.setattr("agent.tools.builtin.image_gen.tool.request_image", respond)
    events = list(conv.stream("画图", run_id="image"))
    assert len([e for e in events if isinstance(e, AssistantImage)]) == 1
    assert len(files.files(conv.session.session_id)) == 1
    assert calls == ([] if native_first else ["draw"])


def test_image_only_continuation_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Generate(NativeModel):
        spec = SPEC

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            self.received.append(request.messages)
            if len(self.received) == 2:
                raise LLMEmptyResponseError("empty")
            assert len(self.received) == 1
            args = json.dumps({"prompt": "red circle"})
            yield LLMToolCallDelta(0, "call", "imagegen", args)
            yield LLMTurnDone(
                "tool_use",
                assistant_turn=ProviderAssistantTurn(
                    None, (ProviderToolCall(0, "call", "imagegen", args, 0),)
                ),
            )

    model = Generate()
    conv = _make_conversation(tmp_path, fake_llm=model)
    assets = AttachmentStore(tmp_path / "assets")
    files = SessionFileService(conv._store, assets, tmp_path / "workspaces")
    conv._tool_registry = ToolRegistry([ImagegenTool(files, FilesystemService())])
    calls = []

    def respond(*args: Any) -> bytes:
        calls.append(args[1:4])
        return png()

    monkeypatch.setattr("agent.tools.builtin.image_gen.tool.request_image", respond)
    events = list(conv.stream("画一个圆", run_id="generate", user_event_id="u1"))
    assert len([e for e in events if isinstance(e, AssistantImage)]) == 1
    assert [e.stop_reason for e in events if isinstance(e, TurnDone)] == ["end_turn"]
    assert len(model.received) == 2
    (ref,) = files.files(conv.session.session_id)
    assert ref.file_ref in str(model.received[-1])
    assert files.read_bytes(conv.session.session_id, ref.file_ref, max_bytes=1000)[0] == png()
    replay = list(conv.stream("画一个圆", run_id="replay", user_event_id="u1"))
    assert len([e for e in replay if isinstance(e, AssistantImage)]) == 1
    assert calls == [(SPEC.images, SPEC.api_key, "red circle")]
    assert len(model.received) == 2


def test_authorized_file_upload_and_recent_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = _make_conversation(tmp_path, fake_llm=NativeModel(script=[items()]))
    assets = AttachmentStore(tmp_path / "assets")
    _, upload = assets.import_image_bytes(png(), display_name="uploaded.png")
    buffer = io.BytesIO()
    Image.new("RGBA", (2, 3), (0, 0, 0, 0)).save(buffer, format="PNG")
    transparent_png = buffer.getvalue()
    _, second = assets.import_image_bytes(transparent_png, display_name="second.png")
    list(conv.stream("见附件", attachments=[upload, second]))
    files = SessionFileService(conv._store, assets, tmp_path / "workspaces")
    tool = ImagegenTool(files, FilesystemService(_policy(tmp_path)))
    session = conv.session.session_id
    args = {
        FILESYSTEM_SESSION_ID_ARG: session,
        FILESYSTEM_TOOL_CALL_ID_ARG: "call",
        IMAGE_SPEC_ARG: SPEC,
        "prompt": "edit",
    }
    sent = []

    def respond(*req: Any) -> bytes:
        sent.append(req[4])
        return transparent_png if req[5] else png()

    monkeypatch.setattr("agent.tools.builtin.image_gen.tool.request_image", respond)
    transparent = tool.invoke(
        {**args, "num_last_images_to_include": 2, "transparent_background": True}
    )
    assert transparent.generated_image
    with assets.open(transparent.generated_image.asset_id) as stream:
        assert stream.read() == transparent_png
    assert [v[1] for v in sent.pop()] == [png(), transparent_png]
    first = files.files(session)[0]
    assert tool.invoke({**args, "reference_images": [{"file_ref": first.file_ref}]}).generated_image
    sent.clear()
    assert tool.invoke({**args, "reference_images": [{"file_ref": "file-" + "0" * 64}]}).is_error
    assert tool.invoke({**args, "num_last_images_to_include": 3}).is_error
    ledger = FilesystemAuthorizationLedger()
    (tmp_path / "original.png").write_bytes(png())
    scope = _grant(ledger, tmp_path, "read_image")
    disk = {"reference_images": [{"filesystem_scope_id": scope, "path": "original.png"}]}
    assert tool.invoke({**args, **disk}).is_error
    assert sent == []
    assert tool.invoke({**args, **disk, FILESYSTEM_LEDGER_ARG: ledger}).generated_image
    assert sent[0][0][1] == png()
    registry = ToolRegistry([tool])
    for invalid in (
        {"num_last_images_to_include": 6},
        {**disk, "num_last_images_to_include": 1},
        {"reference_images": disk["reference_images"] * 6},
    ):
        assert registry.validate("imagegen", {"prompt": "edit", **invalid})


@pytest.mark.parametrize("failure", ["invalid_bytes", "storage", "cancel"])
def test_no_image_delivered_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    conv = _make_conversation(tmp_path, fake_llm=NativeModel())
    assets = AttachmentStore(tmp_path / "assets")
    tool = ImagegenTool(SessionFileService(conv._store, assets, tmp_path), FilesystemService())
    control = ToolInvocationControl()

    def respond(*_: Any) -> bytes:
        if failure == "cancel":
            control.cancel("user stopped")
        return b"invalid" if failure == "invalid_bytes" else png()

    def fail_store(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr("agent.tools.builtin.image_gen.tool.request_image", respond)
    if failure == "storage":
        monkeypatch.setattr(assets, "import_image_bytes", fail_store)
    args = {
        FILESYSTEM_SESSION_ID_ARG: conv.session.session_id,
        FILESYSTEM_TOOL_CALL_ID_ARG: "call",
        IMAGE_SPEC_ARG: SPEC,
        "prompt": "draw",
        TOOL_INVOCATION_CONTROL_ARG: control,
    }
    if failure == "cancel":
        with pytest.raises(ToolInvocationCancelled):
            tool.invoke(args)
    else:
        result = tool.invoke(args)
        assert result.is_error and result.generated_image is None
        assert result.meta["error_kind"] == (
            "image_invalid" if failure == "invalid_bytes" else "image_storage_failed"
        )

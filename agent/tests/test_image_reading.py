from __future__ import annotations

import io
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent.attachments import AttachmentError, AttachmentStore
from agent.context.budget import estimate_tokens
from agent.context.summary_prompt import render_transcript
from agent.context.tool_projection import _project_message
from agent.filesystem import FilesystemAuthorizationLedger, FilesystemService, ProtectedPathPolicy
from agent.filesystem.tools import FILESYSTEM_LEDGER_ARG
from agent.image_reading import ImageReadingService
from agent.image_reading.service import validate_image
from agent.sessions import JsonlSessionStore
from agent.sessions.model_history import SessionModelHistory
from agent.tools import ToolRegistry, ToolResult
from agent.tools.builtin.tool_result_recovery import ToolResultRecoveryTool
from agent.tools.builtin.view_image import ViewImageTool
from agent.tools.protocol import IMAGE_INPUT_SUPPORTED_ARG
from PIL import Image

from agent import Event, Message, Session


def png(format: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), (255, 0, 0)).save(buffer, format=format)
    return buffer.getvalue()


def setup_tool(
    tmp_path: Path,
) -> tuple[ViewImageTool, AttachmentStore, FilesystemAuthorizationLedger]:
    store = AttachmentStore(tmp_path / "assets")
    policy = ProtectedPathPolicy(product_roots=(), personal_skill_roots=(), builtin_skill_roots=())
    tool = ViewImageTool(FilesystemService(policy), ImageReadingService(store))
    return tool, store, FilesystemAuthorizationLedger()


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_static_formats_are_actually_decoded(format: str) -> None:
    assert validate_image(png(format)) == (3, 2)


@pytest.mark.parametrize(
    "data",
    [b"not image", b"\x89PNG\r\n\x1a\n", b"", b"x" * (8 * 1024 * 1024 + 1)],
    ids=["text", "truncated", "empty", "oversize"],
)
def test_bad_images_are_rejected(data: bytes) -> None:
    with pytest.raises(AttachmentError):
        validate_image(data)


def test_webp_canvas_limit_precedes_native_decoder() -> None:
    header = b"RIFF" + struct.pack("<I", 22) + b"WEBPVP8X" + struct.pack("<I", 10)
    data = header + b"\0" * 4 + (8192).to_bytes(3, "little") + (8192).to_bytes(3, "little")
    with pytest.raises(AttachmentError, match="尺寸超限"):
        validate_image(data)


def test_animation_rejected() -> None:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        output,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), "blue")],
    )
    with pytest.raises(AttachmentError, match="静态"):
        validate_image(output.getvalue())


def test_authorization_snapshot_and_lease(tmp_path: Path) -> None:
    tool, store, ledger = setup_tool(tmp_path)
    path = tmp_path / "source.png"
    original = png()
    path.write_bytes(original)
    text_scope = ledger.grant_scope(
        root=path, root_kind="file", permissions=frozenset({"read_text"})
    )
    args = {
        "filesystem_scope_id": text_scope.scope_id,
        "path": ".",
        FILESYSTEM_LEDGER_ARG: ledger,
        IMAGE_INPUT_SUPPORTED_ARG: True,
    }
    assert tool.invoke(args).meta["error_kind"] == "permission_denied"
    scope = ledger.grant_scope(root=path, root_kind="file", permissions=frozenset({"read_image"}))
    args["filesystem_scope_id"] = scope.scope_id
    assert tool.invoke({**args, "path": "../other.png"}).is_error
    assert (
        tool.invoke({**args, IMAGE_INPUT_SUPPORTED_ARG: False}).meta["error_kind"]
        == "image_model_unsupported"
    )
    result = tool.invoke(args)
    assert not result.is_error
    path.unlink()
    assert store.open(result.images[0].asset_id).read() == original
    assert store.reconcile(set(), grace_seconds=0)["removed_assets"] == 0
    assert "base64" not in result.text
    assert (
        ToolRegistry([tool]).to_openai_tools()[0]["function"]["parameters"]["additionalProperties"]
        is False
    )


def test_tool_history_and_recovery_keep_image_origin(tmp_path: Path) -> None:
    image = (
        ImageReadingService(AttachmentStore(tmp_path / "assets"))
        .snapshot(png(), display_name="a.png")
        .image
    )
    event = Event(
        type="tool_call_result",
        uuid="result-image",
        ts=datetime.now(UTC),
        payload={
            "tool_call_id": "call-image",
            "tool_name": "view_image",
            "content": "snapshot",
            "is_error": False,
            "images": [image.to_dict()],
        },
    )
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(title="images", persona="default", model="m")
    store.create(session)
    store.append_event(session.session_id, event)
    session.append(event)
    message = session.messages[-1]
    assert message.images == (image,)
    assert Message.from_dict(message.to_dict()).images == (image,)
    assert "未提供" in message.to_openai()["content"]
    assert "base64" not in str(message.to_dict())
    history = SessionModelHistory()
    history.append_tool_result(
        content="snapshot",
        images=(image,),
        tool_call_id="call-image",
        tool_name="view_image",
        timestamp=event.ts,
        event_uuid=event.uuid,
        is_error=False,
    )
    assert history.items[-1].images == (image,)
    recovery = ToolResultRecoveryTool(store)
    args = {
        "mode": "images",
        "result_ref": event.uuid,
        "__agent_friend_current_session_id": session.session_id,
        IMAGE_INPUT_SUPPORTED_ARG: True,
    }
    assert recovery.invoke(args).images == (image,)
    assert recovery.invoke({**args, "offset": 0}).is_error
    assert recovery.invoke({**args, "result_ref": "other-session-image"}).is_error
    assert recovery.invoke({**args, IMAGE_INPUT_SUPPORTED_ARG: False}).is_error
    assert recovery.invoke({**args, "mode": "text"}).images == ()


def test_projection_and_summary_never_imply_image_observation(tmp_path: Path) -> None:
    image = (
        ImageReadingService(AttachmentStore(tmp_path / "assets"))
        .snapshot(png(), display_name="a.png")
        .image
    )
    message = Message(role="tool", content="size only", images=(image,), uuid="original")
    projected = _project_message(
        message, "cleared", stage="low_water_placeholder", original_tokens=17000
    )
    assert projected.images == () and message.images == (image,)
    assert "result_ref=original" in projected.content
    assert estimate_tokens([projected]) < estimate_tokens([message])
    assert "不得推断原图" in render_transcript([message])
    with pytest.raises(ValueError):
        ToolResult("failed", is_error=True, images=(image,))
    with pytest.raises(ValueError):
        Message(role="user", content="", images=(image,))


def test_hard_image_count_cap_is_projection_only(tmp_path: Path) -> None:
    from agent.context.tool_projection import bound_tool_images
    from agent.context.usage_anchor import estimate_rendered_tokens

    image = (
        ImageReadingService(AttachmentStore(tmp_path / "assets"))
        .snapshot(png(), display_name="a.png")
        .image
    )
    messages = [
        Message(role="tool", content="", images=(image,), uuid=str(index)) for index in range(6)
    ]
    projected, omitted = bound_tool_images(messages)
    assert omitted == 2
    assert sum(len(item.images) for item in projected) == 4
    assert all(item.images for item in messages)
    assert not projected[0].images and projected[-1].images
    short = [{"role": "tool", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
    long = [
        {"role": "tool", "content": [{"type": "image_url", "image_url": {"url": "x" * 1000000}}]}
    ]
    assert estimate_rendered_tokens(short) == estimate_rendered_tokens(long) == 16384


def test_gc_marks_tool_image_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from agent_bridge.attachment_reconciliation import reconcile_attachments

    assets = AttachmentStore(tmp_path / "assets", draft_ttl_seconds=-1)
    ref = ImageReadingService(assets).snapshot(png(), display_name="a.png").image
    session = Session.new(title="images", persona="default", model="m")
    session.append(
        Event(
            type="tool_call_result",
            uuid="image-gc",
            ts=datetime.now(UTC),
            payload={"images": [ref.to_dict()]},
        )
    )
    manager = SimpleNamespace(list=lambda **_kwargs: [session], open=lambda _id: session)
    runtime = SimpleNamespace(attachment_store=assets, persistent_session_manager=manager)
    # Reconciliation uses the same mark set independent of retention grace.
    marked = []
    original = assets.reconcile

    def reconcile(ids: set[str]) -> dict[str, int]:
        marked.append(ids)
        return original(ids, grace_seconds=0)

    monkeypatch.setattr(assets, "reconcile", reconcile)
    result = reconcile_attachments(runtime)  # type: ignore[arg-type]
    assert result is not None and result["removed_assets"] == 0
    assert ref.asset_id in marked[0]

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent.attachments import AttachmentStore
from agent.conversation_events import AssistantImage, TextDelta
from agent.generated_images import save_generated_image
from agent.session_files import SessionFileService
from agent.sessions import Event, JsonlSessionStore, restore_rollout
from llm_providers.image_output import LLMImageResult, LLMImageStart
from PIL import Image

from llm_providers import (
    LLMAssistantItemDone,
    LLMAssistantItemStart,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
    ModelRequest,
    ProviderAssistantTurn,
)

from .test_assistant_phase import NativeModel, _make_conversation, items


def png() -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(data, format="PNG")
    return data.getvalue()


def test_picture_is_saved_discoverable_and_not_replayed_as_binary(tmp_path: Path) -> None:
    data = png()
    model = NativeModel(
        script=[
            [
                LLMImageStart("image", 0),
                LLMImageResult("image", 0, data),
                LLMTurnDone("end_turn", assistant_turn=ProviderAssistantTurn(None, image_count=1)),
            ],
            items(),
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=model)
    assets = AttachmentStore(tmp_path / "assets")
    conv._generated_image_store = assets
    events = list(conv.stream("生成图片", run_id="image-run"))
    saved = next(e for e in events if isinstance(e, AssistantImage))
    assert saved.images and saved.error_code is None
    assert not any(isinstance(e, TextDelta) for e in events)
    files = SessionFileService(conv._store, assets, tmp_path / "workspaces")
    file = files.files(conv.session.session_id)[0]
    assert file.source == "assistant_generated" and file.source_message_ref == saved.message_id
    assert files.read_bytes(conv.session.session_id, file.file_ref, max_bytes=1000)[0] == data
    assert assets.reconcile({file.attachment.asset_id}) is not None
    reloaded = conv._store.load(conv.session.session_id)
    assert any(
        e.payload.get("attachments")
        for e in reloaded.active_events
        if e.type == "assistant_message"
    )
    restored = restore_rollout(reloaded)
    history = str([item.to_openai() for item in restored.history.items])
    assert file.file_ref in history and "assistant_generated" in history
    assert "data:image" not in history and "base64" not in history
    list(conv.stream("看看刚才的图", run_id="next"))
    payload = str(model.received[-1])
    assert "assistant_generated" in payload and file.file_ref in payload
    assert "base64" not in payload and "data:image" not in payload
    conv._store.append_event(
        conv.session.session_id,
        Event(
            type="compaction",
            uuid="compact",
            ts=reloaded.created_at,
            payload={"summary": "生成过图片", "covered_through_uuid": saved.message_id},
        ),
    )
    restarted = SessionFileService(
        JsonlSessionStore(tmp_path / "sessions"),
        AttachmentStore(tmp_path / "assets"),
        tmp_path / "workspaces",
    )
    assert restarted.files(conv.session.session_id)[0].source == "assistant_generated"
    assert restarted.read_bytes(conv.session.session_id, file.file_ref, max_bytes=1000)[0] == data


def test_bad_image_and_store_failures_do_not_claim_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = AttachmentStore(tmp_path)
    _, refs, error = save_generated_image(LLMImageResult("i", 0, b"broken"), assets, "m")
    assert not refs and error == "image_invalid"

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("private disk path")

    monkeypatch.setattr(assets, "import_image_bytes", fail)
    text, refs, error = save_generated_image(LLMImageResult("i", 0, png()), assets, "m")
    assert error == "image_storage_failed" and not refs and "private" not in text


def test_mixed_order_survives_reload_and_idempotent_replay(tmp_path: Path) -> None:
    model = NativeModel(
        script=[
            [
                LLMAssistantItemStart("before", 0, "commentary"),
                LLMTextDelta("before", item_id="before", output_index=0, phase="commentary"),
                LLMAssistantItemDone("before", 0, "commentary"),
                LLMImageStart("image", 1),
                LLMImageResult("image", 1, png()),
                LLMAssistantItemStart("after", 2, "final_answer"),
                LLMTextDelta("after", item_id="after", output_index=2, phase="final_answer"),
                LLMAssistantItemDone("after", 2, "final_answer"),
                LLMTurnDone(
                    "end_turn", assistant_turn=ProviderAssistantTurn("beforeafter", image_count=1)
                ),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=model)
    conv._generated_image_store = AttachmentStore(tmp_path / "assets")
    list(conv.stream("image", run_id="run", user_event_id="user"))
    loaded = conv._store.load(conv.session.session_id)
    messages = [m.content for m in loaded.messages if m.role == "assistant"]
    assert messages[0] == "before" and messages[2] == "after"
    assert "assistant_generated" in messages[1]
    replay = list(conv.stream("image", run_id="replay", user_event_id="user"))
    output = [e for e in replay if isinstance(e, (AssistantImage, TextDelta))]
    assert [type(e) for e in output] == [TextDelta, AssistantImage, TextDelta]
    assert model.turn_idx == 1


@pytest.mark.parametrize("completed", [False, True])
def test_interrupted_generation_is_durable_without_regenerating(
    tmp_path: Path, completed: bool
) -> None:
    class Interrupted(NativeModel):
        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            yield LLMImageStart("unfinished", 0)
            if completed:
                yield LLMImageResult("unfinished", 0, png())
            raise RuntimeError("transport failed")

    conv = _make_conversation(tmp_path, fake_llm=Interrupted())
    conv._generated_image_store = AttachmentStore(tmp_path / "assets")
    with pytest.raises(RuntimeError, match="transport failed"):
        list(conv.stream("image", run_id="interrupted"))
    stored = conv._store.load(conv.session.session_id)
    images = [e for e in stored.active_events if "generated_image" in e.payload]
    assert len(images) == 1
    assert images[0].payload["generated_image"]["error_code"] == (
        None if completed else "image_interrupted"
    )
    assert bool(images[0].payload["attachments"]) == completed

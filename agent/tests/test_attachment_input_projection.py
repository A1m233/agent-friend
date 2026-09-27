from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from agent.attachments.input_policy import AttachmentInputPolicy
from agent.attachments.input_projection import AttachmentInputProjector, project_pdf_message
from agent.context import SummarizingContextManager, estimate_tokens
from agent.context.usage_anchor import RequestMeasurement, UsageAnchor, estimate_rendered_tokens
from agent.session_files import SessionFileService
from llm_providers.catalog import DEEPSEEK_VISION_CAPABILITIES, TEXT_ONLY_CAPABILITIES

from agent import AttachmentRef, AttachmentStore, Conversation, Message, TextDelta
from llm_providers import LLMClient, LLMUsage, MultimodalInputAdapter, ProviderSpec

from .test_context_summarizing import _make_conversation
from .test_context_usage_anchor import MeasuredLLM


def ref(data: bytes, media: str = "application/pdf") -> AttachmentRef:
    digest = hashlib.sha256(data).hexdigest()
    return AttachmentRef(
        digest,
        "report.pdf" if media.endswith("pdf") else "report.txt",
        "document",
        media,
        len(data),
        digest,
    )


def test_pdf_projection_is_bounded_idempotent_and_never_opens_original() -> None:
    def forbidden(_asset: str) -> BytesIO:
        pytest.fail("PDF opened during model-input projection")

    projector = AttachmentInputProjector(forbidden)
    original = Message(role="user", content="read", attachments=[ref(b"%PDF-secret")])
    projected = projector(original)
    assert projector(projected) == projected
    assert original.attachments and not projected.attachments
    assert original.uuid == projected.uuid
    assert "session_files" in projected.content and "%PDF-secret" not in projected.content
    large = replace(
        original, attachments=[replace(original.attachments[0], size_bytes=80 * 1024 * 1024)]
    )
    assert abs(estimate_tokens([large]) - estimate_tokens([original])) < 20
    assert estimate_tokens([projector(large)]) < 1000


def test_text_above_64k_counts_actual_content_before_any_provider_call() -> None:
    data = ("中文 sentence\n" * 15000).encode()
    projector = AttachmentInputProjector(lambda _asset: BytesIO(data))
    original = Message(role="user", content="read", attachments=[ref(data, "text/plain")])
    projected = projector(original)
    assert not projected.attachments and original.attachments
    assert estimate_tokens([projected]) > 64000
    assert estimate_rendered_tokens([projected.to_openai()]) == estimate_tokens([projected])
    assert "中文 sentence" in projected.content


def test_text_integrity_failure_is_not_hidden_by_projection() -> None:
    from agent.attachments import AttachmentError

    projector = AttachmentInputProjector(lambda _asset: BytesIO(b"bad"))
    with pytest.raises(AttachmentError, match="校验失败"):
        projector(Message(role="user", content="", attachments=[ref(b"yes", "text/plain")]))


@pytest.mark.parametrize("model", ["openai/kimi-k2.6", "deepseek/deepseek-v4-pro"])
def test_provider_receives_pdf_hint_without_opening_or_uploading_file(
    tmp_path: Path, model: str
) -> None:
    def forbidden(_asset: str) -> BytesIO:
        pytest.fail("PDF was handed to provider")

    adapter = MultimodalInputAdapter(open_asset=forbidden, cache_dir=tmp_path)
    client = LLMClient(ProviderSpec(model=model, api_key="fixture", context_window=100000))
    message = Message(role="user", content="summarize", attachments=[ref(b"%PDF-secret")])
    wire = adapter.render_message(project_pdf_message(message), client)
    assert isinstance(wire["content"], str)
    assert "session_files" in wire["content"]
    assert "%PDF-secret" not in json.dumps(wire)


@pytest.mark.parametrize("user_text", ["summarize", ""])
def test_first_turn_followup_restart_keep_original_available_without_native_pdf(
    tmp_path: Path,
    user_text: str,
) -> None:
    assets = AttachmentStore(tmp_path / "assets")
    data = b"%PDF-1.7\nprivate fixture body"

    async def chunks() -> AsyncIterator[bytes]:
        yield data

    draft, attachment = asyncio.run(assets.import_stream(chunks(), display_name="report.pdf"))
    assets.consume_draft(draft)
    llm = MeasuredLLM(context_window=100000, prompt_tokens=1000)
    conv, session, store = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    conv._message_projector = AttachmentInputProjector(assets.open)
    assert (
        "".join(
            event.text
            for event in conv.stream(user_text, attachments=[attachment])
            if isinstance(event, TextDelta)
        )
        == "ok"
    )
    assert conv.send("continue") == "ok"
    service = SessionFileService(store, assets, tmp_path / "workspace")
    listed = service.files(session.session_id)
    assert len(listed) == 1
    prepared = service.prepare(session.session_id, listed[0].file_ref)
    assert (Path(prepared["workspace"]) / prepared["relative_path"]).read_bytes() == data
    restarted = Conversation(
        session=store.load(session.session_id),
        store=store,
        llm_client=llm,
        prompt_builder=conv._prompt_builder,
        catalog=conv._catalog,
        context_manager=SummarizingContextManager(),
        message_projector=AttachmentInputProjector(assets.open),
    )
    assert restarted.send("read again") == "ok"
    for request in llm.received:
        rendered = json.dumps(request, ensure_ascii=False)
        assert "private fixture body" not in rendered and "base64," not in rendered
        assert '"type": "file"' not in rendered and '"type": "input_file"' not in rendered
        assert "session_files" in rendered


def test_old_native_usage_cannot_be_reused_for_new_pdf_projection(tmp_path: Path) -> None:
    conv, _, _ = _make_conversation(
        tmp_path, fake_llm=MeasuredLLM(), context_manager=SummarizingContextManager()
    )
    native = [{"role": "user", "content": [{"type": "input_file", "file_id": "old"}]}]
    conv._usage_anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(native, "old", compatibility_scope="pre-projection", tools=None),
        LLMUsage(prompt_tokens=385602, input_token_accounting="inclusive_total"),
    )
    result = conv._estimate_context_input(
        [Message(role="user", content="read", attachments=[ref(b"%PDF-fixture")])], None
    )
    assert result.reason == "request_scope_changed"
    assert result.tokens < 1000 and result.anchor_tokens == 0


def test_text_model_accepts_session_pdf_but_not_native_image() -> None:
    policy = AttachmentInputPolicy(TEXT_ONLY_CAPABILITIES, True)
    policy.validate([ref(b"%PDF-fixture")])
    assert policy.public_payload()["deliveryByMediaType"] == {"application/pdf": "session_file"}
    from agent.attachments import AttachmentError

    with pytest.raises(AttachmentError, match="不支持"):
        policy.validate([replace(ref(b"png"), kind="image", media_type="image/png")])
    with pytest.raises(AttachmentError, match="文件处理能力不可用"):
        AttachmentInputPolicy(TEXT_ONLY_CAPABILITIES).validate([ref(b"%PDF-fixture")])


def test_pdf_does_not_consume_provider_native_subset_limit() -> None:
    native = replace(
        DEEPSEEK_VISION_CAPABILITIES, max_attachments_per_turn=1, max_total_attachment_bytes=5
    )
    policy = AttachmentInputPolicy(native, True)
    image = replace(ref(b"png"), kind="image", media_type="image/png")
    policy.validate([image, ref(b"%PDF-larger-than-native-total")])
    from agent.attachments import AttachmentError

    with pytest.raises(AttachmentError, match="数量"):
        policy.validate([image, image])


def test_calibration_isolated_between_pdf_text_and_images(tmp_path: Path) -> None:
    conv, _, _ = _make_conversation(
        tmp_path, fake_llm=MeasuredLLM(), context_manager=SummarizingContextManager()
    )
    plain = [Message(role="user", content="read")]
    pdf = [replace(plain[0], attachments=[ref(b"%PDF-fixture")])]
    image = [
        replace(plain[0], attachments=[replace(ref(b"png"), kind="image", media_type="image/png")])
    ]
    conv._calibration_by_input[conv._input_calibration_key(image)] = 4.0
    assert len({conv._input_calibration_key(messages) for messages in (plain, pdf, image)}) == 3
    assert conv._estimate_context_input(pdf, None).tokens < 1000


def test_cached_text_detects_same_size_source_change(tmp_path: Path) -> None:
    import os

    from agent.attachments import AttachmentError

    path = tmp_path / "source.txt"
    path.write_bytes(b"old")
    projector = AttachmentInputProjector(lambda _: path.open("rb"))
    message = Message(role="user", content="", attachments=[ref(b"old", "text/plain")])
    assert "old" in projector(message).content
    original = path.stat()
    path.write_bytes(b"new")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000))
    with pytest.raises(AttachmentError, match="校验失败"):
        projector(message)

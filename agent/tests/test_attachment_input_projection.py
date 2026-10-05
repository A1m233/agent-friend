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
from agent.attachments.input_projection import project_file_message
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

    projector = project_file_message
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


@pytest.mark.parametrize(
    "media", ["text/plain", "image/png", "application/zip", "application/octet-stream"]
)
def test_all_files_are_bounded_references(media: str) -> None:
    data = b"private-body" * 20000
    original = Message(role="user", content="read", attachments=[ref(data, media)])
    projected = project_file_message(original)
    assert projected.attachments == [] and original.attachments
    assert "private-body" not in projected.content
    assert estimate_tokens([projected]) < 1000
    assert estimate_rendered_tokens([projected.to_openai()]) == estimate_tokens([projected])


@pytest.mark.parametrize("model", ["openai/kimi-k2.6", "deepseek/deepseek-v4-pro"])
def test_provider_receives_pdf_hint_without_opening_or_uploading_file(
    tmp_path: Path, model: str
) -> None:
    def forbidden(_asset: str) -> BytesIO:
        pytest.fail("PDF was handed to provider")

    adapter = MultimodalInputAdapter(open_asset=forbidden)
    client = LLMClient(ProviderSpec(model=model, api_key="fixture", context_window=100000))
    message = Message(role="user", content="summarize", attachments=[ref(b"%PDF-secret")])
    wire = adapter.render_message(project_file_message(message), client)
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
    conv._message_projector = project_file_message
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
        message_projector=project_file_message,
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


def test_text_model_accepts_all_session_files_with_application_limits() -> None:
    from agent.attachments import AttachmentError

    policy = AttachmentInputPolicy(TEXT_ONLY_CAPABILITIES.tool_calling, True)
    files = [ref(b"data", media) for media in ("application/pdf", "image/png", "application/zip")]
    policy.validate(files)
    assert policy.public_payload()["acceptsAnyFile"] is True
    with pytest.raises(AttachmentError, match="文件处理能力不可用"):
        AttachmentInputPolicy(TEXT_ONLY_CAPABILITIES.tool_calling).validate(files)
    with pytest.raises(AttachmentError, match="10"):
        policy.validate(files * 4)


def test_files_do_not_consume_provider_native_limits() -> None:
    native = replace(
        DEEPSEEK_VISION_CAPABILITIES, max_attachments_per_turn=1, max_total_attachment_bytes=5
    )
    AttachmentInputPolicy(native.tool_calling, True).validate(
        [ref(b"large" * 20), ref(b"large" * 20)]
    )


def test_calibration_is_shared_by_file_types_and_separate_from_plain_text(tmp_path: Path) -> None:
    conv, _, _ = _make_conversation(
        tmp_path, fake_llm=MeasuredLLM(), context_manager=SummarizingContextManager()
    )
    plain = Message(role="user", content="read")
    pdf = replace(plain, attachments=[ref(b"pdf")])
    image = replace(plain, attachments=[ref(b"image", "image/png")])
    assert conv._input_calibration_key([pdf]) == conv._input_calibration_key([image])
    assert conv._input_calibration_key([plain]) != conv._input_calibration_key([pdf])

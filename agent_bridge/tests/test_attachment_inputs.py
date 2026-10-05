from __future__ import annotations

from typing import cast

import pytest
from agent_bridge.attachment_inputs import ExistingAttachmentInput, prepare_attachment_input
from fastapi import HTTPException

from agent import AttachmentRef, AttachmentStore


class _VerifyingStore:
    max_attachment_bytes = 100 * 1024 * 1024

    def verify(self, _asset_id: str) -> None:
        return None


def _image(size_bytes: int) -> AttachmentRef:
    return AttachmentRef(
        asset_id="asset-1",
        display_name="sample.png",
        kind="image",
        media_type="image/png",
        size_bytes=size_bytes,
        sha256="0" * 64,
    )


def test_deepseek_vision_admission_enforces_application_single_file_limit() -> None:
    store = cast(AttachmentStore, _VerifyingStore())

    accepted = prepare_attachment_input(
        [ExistingAttachmentInput(_image(100 * 1024 * 1024))],
        model_id="deepseek/deepseek-flash",
        store=store,
        session_files_available=True,
    )
    assert accepted.validated_refs[0].size_bytes == 100 * 1024 * 1024

    with pytest.raises(HTTPException) as caught:
        prepare_attachment_input(
            [ExistingAttachmentInput(_image(100 * 1024 * 1024 + 1))],
            model_id="deepseek/deepseek-flash",
            store=store,
            session_files_available=True,
        )

    detail = caught.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == "attachment_size_limit_exceeded"


def test_pdf_admission_uses_session_files_without_enabling_native_images() -> None:
    from dataclasses import replace

    store = cast(AttachmentStore, _VerifyingStore())
    pdf = replace(_image(80 * 1024 * 1024), kind="document", media_type="application/pdf")
    plan = prepare_attachment_input(
        [ExistingAttachmentInput(pdf)],
        model_id="deepseek/deepseek-v4-pro",
        store=store,
        session_files_available=True,
    )
    assert plan.resolve() == [pdf]
    with pytest.raises(HTTPException) as unavailable:
        prepare_attachment_input(
            [ExistingAttachmentInput(pdf)], model_id="deepseek/deepseek-v4-pro", store=store
        )
    assert isinstance(unavailable.value.detail, dict)
    assert unavailable.value.detail["error"] == "attachment_file_processing_unavailable"
    image = _image(1)
    plan = prepare_attachment_input(
        [ExistingAttachmentInput(image)],
        model_id="deepseek/deepseek-v4-pro",
        store=store,
        session_files_available=True,
    )
    assert plan.resolve() == [image]

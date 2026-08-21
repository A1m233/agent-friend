from __future__ import annotations

from typing import cast

import pytest
from agent_bridge.attachment_inputs import ExistingAttachmentInput, prepare_attachment_input
from fastapi import HTTPException

from agent import AttachmentRef, AttachmentStore


class _VerifyingStore:
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


def test_deepseek_vision_admission_enforces_model_single_file_limit() -> None:
    store = cast(AttachmentStore, _VerifyingStore())

    accepted = prepare_attachment_input(
        [ExistingAttachmentInput(_image(64 * 1024 * 1024))],
        model_id="deepseek/deepseek-v4-flash-vision-exp",
        store=store,
    )
    assert accepted.validated_refs[0].size_bytes == 64 * 1024 * 1024

    with pytest.raises(HTTPException) as caught:
        prepare_attachment_input(
            [ExistingAttachmentInput(_image(64 * 1024 * 1024 + 1))],
            model_id="deepseek/deepseek-v4-flash-vision-exp",
            store=store,
        )

    detail = caught.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == "attachment_size_limit_exceeded"

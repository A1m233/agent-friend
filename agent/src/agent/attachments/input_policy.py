"""Application attachment admission, distinct from provider-native modalities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm_providers.catalog import ModelRouteCapabilities

from .models import AttachmentRef
from .store import MAX_ATTACHMENT_BYTES, AttachmentError

MAX_INPUT_ATTACHMENTS = 10
MAX_TOTAL_INPUT_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class AttachmentInputPolicy:
    native: ModelRouteCapabilities
    session_files_available: bool = False
    file_max_bytes: int = MAX_ATTACHMENT_BYTES

    @property
    def session_pdf(self) -> bool:
        return self.session_files_available and self.native.tool_calling

    @property
    def media_types(self) -> tuple[str, ...]:
        types = [kind for kind in self.native.accepted_media_types if kind != "application/pdf"]
        if self.session_pdf:
            types.append("application/pdf")
        return tuple(types)

    def file_limit(self, media_type: str) -> int:
        return min(
            self.file_max_bytes,
            MAX_ATTACHMENT_BYTES
            if media_type == "application/pdf"
            else self.native.max_attachment_bytes,
        )

    def validate(self, refs: list[AttachmentRef]) -> None:
        if len(refs) > MAX_INPUT_ATTACHMENTS:
            raise AttachmentError("attachment_turn_limit_exceeded", "单轮最多添加 10 个附件。")
        native_refs = []
        for ref in refs:
            if ref.media_type == "application/pdf":
                if not self.session_pdf:
                    raise AttachmentError(
                        "attachment_file_processing_unavailable",
                        "当前会话的文件处理能力不可用，请检查命令运行环境及模型工具调用能力。",
                    )
            else:
                if ref.media_type not in self.native.accepted_media_types:
                    raise AttachmentError(
                        "attachment_type_unsupported", "当前模型不支持此附件类型。"
                    )
                native_refs.append(ref)
            if ref.size_bytes > self.file_limit(ref.media_type):
                raise AttachmentError("attachment_size_limit_exceeded", "单个附件大小超过限制。")
        if len(native_refs) > self.native.max_attachments_per_turn:
            raise AttachmentError(
                "attachment_turn_limit_exceeded", "原生附件数量超过当前模型限制。"
            )
        if (
            sum(ref.size_bytes for ref in refs) > MAX_TOTAL_INPUT_BYTES
            or sum(ref.size_bytes for ref in native_refs) > self.native.max_total_attachment_bytes
        ):
            raise AttachmentError("attachment_total_limit_exceeded", "本轮附件总大小超过限制。")

    def public_payload(self) -> dict[str, Any]:
        limits = {kind: self.file_limit(kind) for kind in self.media_types}
        return {
            "attachments": bool(limits),
            "acceptedMediaTypes": list(limits),
            "maxAttachmentBytes": max(limits.values(), default=0),
            "maxAttachmentBytesByType": limits,
            "maxAttachmentsPerTurn": MAX_INPUT_ATTACHMENTS,
            "maxTotalAttachmentBytes": MAX_TOTAL_INPUT_BYTES,
            "nativeMaxAttachmentsPerTurn": self.native.max_attachments_per_turn,
            "nativeMaxTotalAttachmentBytes": self.native.max_total_attachment_bytes,
            "deliveryByMediaType": {
                kind: "session_file" if kind == "application/pdf" else "native" for kind in limits
            },
        }

"""Application file admission, independent of provider-native modalities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import AttachmentRef
from .store import MAX_ATTACHMENT_BYTES, AttachmentError

MAX_INPUT_ATTACHMENTS = 10
MAX_TOTAL_INPUT_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class AttachmentInputPolicy:
    tool_calling: bool
    session_files_available: bool = False
    file_max_bytes: int = MAX_ATTACHMENT_BYTES

    @property
    def available(self) -> bool:
        return self.session_files_available and self.tool_calling

    def validate(self, refs: list[AttachmentRef]) -> None:
        if refs and not self.available:
            raise AttachmentError(
                "attachment_file_processing_unavailable",
                "当前会话的文件处理能力不可用，请检查命令运行环境及模型工具调用能力。",
            )
        if len(refs) > MAX_INPUT_ATTACHMENTS:
            raise AttachmentError("attachment_turn_limit_exceeded", "单轮最多添加 10 个附件。")
        if any(ref.size_bytes < 0 or ref.size_bytes > self.file_max_bytes for ref in refs):
            raise AttachmentError("attachment_size_limit_exceeded", "单个附件大小超过限制。")
        if sum(ref.size_bytes for ref in refs) > MAX_TOTAL_INPUT_BYTES:
            raise AttachmentError("attachment_total_limit_exceeded", "本轮附件总大小超过限制。")

    def public_payload(self) -> dict[str, Any]:
        return {
            "attachments": self.available,
            "acceptsAnyFile": True,
            "maxAttachmentBytes": self.file_max_bytes,
            "maxAttachmentsPerTurn": MAX_INPUT_ATTACHMENTS,
            "maxTotalAttachmentBytes": MAX_TOTAL_INPUT_BYTES,
        }

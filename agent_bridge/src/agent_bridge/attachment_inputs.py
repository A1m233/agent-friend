"""Bridge-local attachment admission shared by normal and edit-resend turns."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from llm_providers.catalog import ModelRouteCapabilities

from agent import AttachmentError, AttachmentRef, AttachmentStore
from llm_providers import model_capabilities


@dataclass(frozen=True)
class ExistingAttachmentInput:
    ref: AttachmentRef


@dataclass(frozen=True)
class DraftAttachmentInput:
    draft_id: str
    kind: Literal["image", "document"]


AttachmentInput = ExistingAttachmentInput | DraftAttachmentInput


@dataclass(frozen=True)
class _PreparedAttachment:
    validated_ref: AttachmentRef
    draft_index: int | None


class AttachmentInputPlan:
    """Validated ordered input whose draft leases are consumed at most once."""

    def __init__(
        self,
        entries: list[_PreparedAttachment],
        draft_ids: list[str],
        store: AttachmentStore | None,
    ) -> None:
        self._entries = tuple(entries)
        self._draft_ids = tuple(draft_ids)
        self._store = store
        self._lock = threading.Lock()
        self._resolved: list[AttachmentRef] | None = None

    @property
    def validated_refs(self) -> list[AttachmentRef]:
        return [entry.validated_ref for entry in self._entries]

    def resolve(self) -> list[AttachmentRef]:
        """Consume draft leases once and rebuild the caller-provided order."""

        with self._lock:
            if self._resolved is not None:
                return list(self._resolved)
            consumed = (
                self._store.consume_drafts(list(self._draft_ids))
                if self._draft_ids and self._store is not None
                else []
            )
            self._resolved = [
                consumed[entry.draft_index]
                if entry.draft_index is not None
                else entry.validated_ref
                for entry in self._entries
            ]
            return list(self._resolved)


def _attachment_http_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": getattr(exc, "code", "attachment_draft_expired"),
            "message": str(exc),
        },
    )


def prepare_attachment_input(
    entries: list[AttachmentInput],
    *,
    model_id: str,
    store: AttachmentStore | None,
    capability_resolver: Callable[[str], ModelRouteCapabilities] = model_capabilities,
    cancel_check: Callable[[], None] | None = None,
) -> AttachmentInputPlan:
    """Validate attachment identities and model limits before SSE admission."""

    if cancel_check is not None:
        cancel_check()
    capabilities = capability_resolver(model_id)
    if entries and not capabilities.attachments:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "attachment_model_unsupported",
                "message": "当前模型不支持附件，请切换到支持图片或文档的模型。",
            },
        )
    if len(entries) > capabilities.max_attachments_per_turn:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "attachment_turn_limit_exceeded",
                "message": f"单轮最多添加 {capabilities.max_attachments_per_turn} 个附件。",
            },
        )
    if entries and store is None:
        raise HTTPException(status_code=503, detail="attachment store unavailable")

    prepared: list[_PreparedAttachment] = []
    draft_ids: list[str] = []
    for entry in entries:
        if cancel_check is not None:
            cancel_check()
        try:
            if isinstance(entry, ExistingAttachmentInput):
                assert store is not None
                store.verify(entry.ref.asset_id)
                ref = entry.ref
                draft_index = None
            else:
                assert store is not None
                ref = store.draft(entry.draft_id)
                draft_index = len(draft_ids)
                draft_ids.append(entry.draft_id)
        except Exception as exc:
            if cancel_check is not None:
                cancel_check()
            raise _attachment_http_error(exc) from exc

        if (
            isinstance(entry, DraftAttachmentInput) and ref.kind != entry.kind
        ) or ref.media_type not in capabilities.accepted_media_types:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "attachment_type_unsupported",
                    "message": "当前模型不支持此附件类型。",
                },
            )
        if ref.size_bytes > capabilities.max_attachment_bytes:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "attachment_size_limit_exceeded",
                    "message": "单个附件大小超过当前模型限制。",
                },
            )
        prepared.append(_PreparedAttachment(ref, draft_index))

    if (
        sum(entry.validated_ref.size_bytes for entry in prepared)
        > capabilities.max_total_attachment_bytes
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "attachment_total_limit_exceeded",
                "message": "本轮附件总大小超过限制。",
            },
        )

    if cancel_check is not None:
        cancel_check()
    return AttachmentInputPlan(prepared, draft_ids, store)


class AttachmentPreparationError(AttachmentError):
    """Preserve the attachment code when validation happens after run admission."""

    @property
    def reason_code(self) -> str:
        return self.code


def prepare_run_attachment_input(
    entries: list[AttachmentInput],
    *,
    model_id: str,
    store: AttachmentStore | None,
    capability_resolver: Callable[[str], ModelRouteCapabilities] = model_capabilities,
    cancel_check: Callable[[], None],
) -> AttachmentInputPlan:
    try:
        return prepare_attachment_input(
            entries,
            model_id=model_id,
            store=store,
            cancel_check=cancel_check,
            capability_resolver=capability_resolver,
        )
    except HTTPException as exc:
        cancel_check()
        detail = exc.detail
        if isinstance(detail, dict):
            raise AttachmentPreparationError(
                str(detail.get("error", "attachment_draft_expired")),
                str(detail.get("message", "附件暂不可用。")),
            ) from exc
        raise AttachmentPreparationError(
            "attachment_store_unavailable", "附件服务暂不可用，请稍后重试。"
        ) from exc

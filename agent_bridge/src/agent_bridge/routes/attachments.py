"""Loopback-only attachment upload and canonical content routes."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

from agent import AttachmentError, AttachmentStore

from ..assembly import BridgeRuntime


def _error(error: AttachmentError) -> HTTPException:
    status = 413 if error.code == "attachment_file_too_large" else 400
    if error.code in {"attachment_asset_missing", "attachment_draft_expired"}:
        status = 404
    return HTTPException(status_code=status, detail={"error": error.code, "message": str(error)})


def _store(runtime: BridgeRuntime) -> AttachmentStore:
    if runtime.attachment_store is None:
        raise HTTPException(status_code=503, detail="attachment store unavailable")
    return runtime.attachment_store


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1/attachments")

    @router.post("/drafts")
    async def upload_draft(
        request: Request,
        x_filename: str = Header(default="attachment", alias="X-Attachment-Filename"),
        content_type: str | None = Header(default=None, alias="Content-Type"),
    ) -> dict[str, object]:
        try:
            draft_id, ref = await _store(runtime).import_stream(
                request.stream(),
                display_name=unquote(x_filename),
                declared_media_type=content_type,
            )
        except AttachmentError as exc:
            raise _error(exc) from exc
        return {
            "draftId": draft_id,
            "attachment": ref.to_dict(),
            "contentUrl": f"/v1/attachments/drafts/{quote(draft_id)}/content",
            "expiresAt": datetime.fromtimestamp(
                _store(runtime).draft_expires_at(draft_id), tz=UTC
            ).isoformat(),
        }

    @router.delete("/drafts/{draft_id}", status_code=204)
    def release_draft(draft_id: str) -> None:
        try:
            _store(runtime).release_draft(draft_id)
        except AttachmentError as exc:
            raise _error(exc) from exc

    @router.get("/drafts/{draft_id}/content")
    def read_draft(draft_id: str) -> FileResponse:
        try:
            ref = _store(runtime).draft(draft_id)
            path = _store(runtime).verify(ref.asset_id)
        except AttachmentError as exc:
            raise _error(exc) from exc
        return FileResponse(path, media_type=ref.media_type, filename=ref.display_name)

    @router.get("/assets/{asset_id}/content")
    def read_asset(asset_id: str) -> FileResponse:
        try:
            ref = _store(runtime).metadata(asset_id)
            path = _store(runtime).verify(asset_id)
        except AttachmentError as exc:
            raise _error(exc) from exc
        return FileResponse(path, media_type=ref.media_type, filename=ref.display_name)

    @router.get("/assets/{asset_id}")
    def read_asset_metadata(asset_id: str) -> dict[str, object]:
        try:
            ref = _store(runtime).metadata(asset_id)
            _store(runtime).verify(asset_id)
        except AttachmentError as exc:
            raise _error(exc) from exc
        return {"attachment": ref.to_dict(), "available": True}

    app.include_router(router)

from __future__ import annotations

import codecs
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO, Literal

from .models import AttachmentKind, AttachmentRef

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
DEFAULT_DRAFT_TTL_SECONDS = 24 * 60 * 60
DEFAULT_GC_GRACE_SECONDS = 24 * 60 * 60
SUPPORTED_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain"}
)
_CANONICAL_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class AttachmentError(ValueError):
    def __init__(self, code: str, message: str, *, media_type: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.media_type = media_type


def _safe_name(value: str) -> str:
    name = Path(value.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:255] or "attachment"


def _detect_media_type(head: bytes, *, valid_utf8_text: bool) -> tuple[str, AttachmentKind]:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "image"
    if head.startswith((b"GIF87a", b"GIF89a")):
        raise AttachmentError(
            "attachment_type_unsupported",
            "暂不支持 GIF 动图。",
            media_type="image/gif",
        )
    if head.startswith(b"%PDF-"):
        return "application/pdf", "document"
    if valid_utf8_text:
        return "text/plain", "document"
    raise AttachmentError("attachment_type_unsupported", "仅支持 JPG、PNG、WebP、PDF 和 TXT 文件。")


def _extension_media_type(display_name: str) -> str | None:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }.get(Path(display_name).suffix.lower())


def _canonical_image_name(display_name: str, media_type: str) -> str:
    name = _safe_name(display_name)
    extension = _CANONICAL_EXTENSIONS[media_type]
    path = Path(name)
    if path.suffix.lower() == extension:
        return name
    stem = path.stem if path.suffix else path.name
    stem = stem[: 255 - len(extension)] or "attachment"
    return f"{stem}{extension}"


class AttachmentStore:
    """Content-addressed attachment store with short-lived draft leases."""

    def __init__(
        self,
        root: Path,
        *,
        max_attachment_bytes: int = MAX_ATTACHMENT_BYTES,
        draft_ttl_seconds: float = DEFAULT_DRAFT_TTL_SECONDS,
    ) -> None:
        self.root = root
        self.max_attachment_bytes = max_attachment_bytes
        self.draft_ttl_seconds = draft_ttl_seconds
        self._lock = threading.RLock()
        self.blobs_dir = root / "blobs"
        self.metadata_dir = root / "metadata"
        self.drafts_dir = root / "drafts"
        self.tmp_dir = root / "tmp"
        for directory in (self.blobs_dir, self.metadata_dir, self.drafts_dir, self.tmp_dir):
            directory.mkdir(parents=True, exist_ok=True)

    async def import_stream(
        self,
        chunks: AsyncIterator[bytes],
        *,
        display_name: str,
        declared_media_type: str | None = None,
        image_metadata_policy: Literal["strict", "advisory"] = "strict",
    ) -> tuple[str, AttachmentRef]:
        fd, tmp_name = tempfile.mkstemp(prefix="upload-", dir=self.tmp_dir)
        tmp = Path(tmp_name)
        digest = hashlib.sha256()
        size = 0
        head = bytearray()
        text_decoder = codecs.getincrementaldecoder("utf-8")()
        valid_utf8_text = True
        try:
            with os.fdopen(fd, "wb") as output:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_attachment_bytes:
                        raise AttachmentError(
                            "attachment_file_too_large",
                            f"单个附件不能超过 {self.max_attachment_bytes // (1024 * 1024)}MB。",
                        )
                    if len(head) < 8192:
                        head.extend(chunk[: 8192 - len(head)])
                    if valid_utf8_text:
                        if b"\x00" in chunk:
                            valid_utf8_text = False
                        else:
                            try:
                                text_decoder.decode(chunk, final=False)
                            except UnicodeDecodeError:
                                valid_utf8_text = False
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size == 0:
                raise AttachmentError("attachment_type_unsupported", "空文件不能作为附件发送。")
            if valid_utf8_text:
                try:
                    text_decoder.decode(b"", final=True)
                except UnicodeDecodeError:
                    valid_utf8_text = False
            media_type, kind = _detect_media_type(bytes(head), valid_utf8_text=valid_utf8_text)
            declared = (declared_media_type or "").split(";", 1)[0].strip().lower()
            if declared and declared not in ("application/octet-stream", media_type):
                if kind != "image" or image_metadata_policy == "strict":
                    raise AttachmentError(
                        "attachment_type_mismatch", "文件内容与声明的类型不一致。"
                    )
                logger.warning(
                    "Attachment image metadata mismatch "
                    "reason=declared_media_type_mismatch "
                    "declared_media_type=%s detected_media_type=%s",
                    declared,
                    media_type,
                )
            extension_type = _extension_media_type(display_name)
            if (
                extension_type is not None
                and extension_type != media_type
                and (kind != "image" or image_metadata_policy == "strict")
            ):
                raise AttachmentError(
                    "attachment_type_mismatch", "文件扩展名与实际内容类型不一致。"
                )

            normalized_display_name = _safe_name(display_name)
            if kind == "image" and image_metadata_policy == "advisory":
                filename_extension = Path(normalized_display_name).suffix.lower()
                canonical_extension = _CANONICAL_EXTENSIONS[media_type]
                if filename_extension and filename_extension != canonical_extension:
                    logger.warning(
                        "Attachment image metadata mismatch "
                        "reason=filename_extension_mismatch "
                        "filename_extension=%s detected_media_type=%s",
                        filename_extension,
                        media_type,
                    )
                normalized_display_name = _canonical_image_name(display_name, media_type)

            sha256 = digest.hexdigest()
            ref = AttachmentRef(
                asset_id=sha256,
                display_name=normalized_display_name,
                kind=kind,
                media_type=media_type,
                size_bytes=size,
                sha256=sha256,
            )
            return self._commit_import(tmp, ref)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def import_image_bytes(self, data: bytes, *, display_name: str) -> tuple[str, AttachmentRef]:
        """Persist already decoded image bytes; keep a lease until event publication."""
        if not data or len(data) > self.max_attachment_bytes:
            raise AttachmentError("attachment_file_too_large", "图片为空或超过资产大小限制。")
        media_type, kind = _detect_media_type(data[:8192], valid_utf8_text=False)
        if kind != "image":
            raise AttachmentError("attachment_type_unsupported", "需要图像内容。")
        digest = hashlib.sha256(data).hexdigest()
        ref = AttachmentRef(
            digest,
            _canonical_image_name(display_name, media_type),
            kind,
            media_type,
            len(data),
            digest,
        )
        fd, name = tempfile.mkstemp(prefix="image-", dir=self.tmp_dir)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            return self._commit_import(tmp, ref)
        finally:
            tmp.unlink(missing_ok=True)

    def _commit_import(self, tmp: Path, ref: AttachmentRef) -> tuple[str, AttachmentRef]:
        with self._lock:
            blob = self._blob_path(ref.sha256)
            blob.parent.mkdir(parents=True, exist_ok=True)
            if (
                blob.exists()
                and blob.stat().st_size == ref.size_bytes
                and hashlib.sha256(blob.read_bytes()).hexdigest() == ref.sha256
            ):
                tmp.unlink(missing_ok=True)
            else:
                os.replace(tmp, blob)
            self._write_json_atomic(self._metadata_path(ref.sha256), ref.to_dict())
            draft_id = secrets.token_urlsafe(24)
            created_at = time.time()
            self._write_json_atomic(
                self.drafts_dir / f"{draft_id}.json",
                {
                    "draft_id": draft_id,
                    "created_at": created_at,
                    "expires_at": created_at + self.draft_ttl_seconds,
                    "attachment": ref.to_dict(),
                },
            )
        return draft_id, ref

    def consume_draft(self, draft_id: str) -> AttachmentRef:
        return self.consume_drafts([draft_id])[0]

    def consume_drafts(self, draft_ids: list[str]) -> list[AttachmentRef]:
        """Verify a batch and turn leases into retry-safe consumed receipts."""

        with self._lock:
            loaded = [self._load_draft(draft_id) for draft_id in draft_ids]
            for _, ref, _ in loaded:
                self.verify(ref.asset_id)
            for path, ref, expires_at in loaded:
                self._write_json_atomic(
                    path,
                    {
                        "draft_id": path.stem,
                        "consumed_at": time.time(),
                        "expires_at": expires_at,
                        "attachment": ref.to_dict(),
                    },
                )
            return [ref for _, ref, _ in loaded]

    def release_draft(self, draft_id: str) -> None:
        with self._lock:
            self._draft_path(draft_id).unlink(missing_ok=True)

    def draft(self, draft_id: str) -> AttachmentRef:
        with self._lock:
            _, ref, _ = self._load_draft(draft_id)
            return ref

    def draft_expires_at(self, draft_id: str) -> float:
        with self._lock:
            _, _, expires_at = self._load_draft(draft_id)
            return expires_at

    def reconcile(
        self,
        referenced_asset_ids: set[str],
        *,
        now: float | None = None,
        grace_seconds: float = DEFAULT_GC_GRACE_SECONDS,
    ) -> dict[str, int]:
        """Expire abandoned drafts and remove old unreferenced canonical assets."""

        clock = time.time() if now is None else now
        marked = set(referenced_asset_ids)
        expired_drafts = 0
        removed_assets = 0
        with self._lock:
            for path in self.drafts_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    ref = AttachmentRef.from_dict(payload["attachment"])
                    expires_at = float(payload["expires_at"])
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    if path.stat().st_mtime <= clock - grace_seconds:
                        path.unlink(missing_ok=True)
                        expired_drafts += 1
                    continue
                if expires_at <= clock:
                    path.unlink(missing_ok=True)
                    expired_drafts += 1
                else:
                    marked.add(ref.asset_id)

            for metadata_path in self.metadata_dir.glob("*.json"):
                asset_id = metadata_path.stem
                if asset_id in marked or metadata_path.stat().st_mtime > clock - grace_seconds:
                    continue
                blob = self._blob_path(asset_id)
                metadata_path.unlink(missing_ok=True)
                blob.unlink(missing_ok=True)
                if blob.parent != self.blobs_dir:
                    with suppress(OSError):
                        blob.parent.rmdir()
                self._remove_provider_cache(asset_id)
                removed_assets += 1
        return {"expired_drafts": expired_drafts, "removed_assets": removed_assets}

    def _load_draft(self, draft_id: str) -> tuple[Path, AttachmentRef, float]:
        path = self._draft_path(draft_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            ref = AttachmentRef.from_dict(payload["attachment"])
            expires_at = float(payload["expires_at"])
        except FileNotFoundError as exc:
            raise AttachmentError(
                "attachment_draft_expired", "附件草稿已失效，请重新添加。"
            ) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AttachmentError(
                "attachment_integrity_failed", "附件草稿已损坏，请重新添加。"
            ) from exc
        if expires_at <= time.time():
            path.unlink(missing_ok=True)
            raise AttachmentError("attachment_draft_expired", "附件草稿已过期，请重新添加。")
        return path, ref, expires_at

    def _remove_provider_cache(self, asset_id: str) -> None:
        cache_dir = self.root / "provider-cache"
        if not cache_dir.is_dir():
            return
        for path in cache_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("_asset_id") == asset_id:
                path.unlink(missing_ok=True)

    def metadata(self, asset_id: str) -> AttachmentRef:
        try:
            payload = json.loads(self._metadata_path(asset_id).read_text(encoding="utf-8"))
            return AttachmentRef.from_dict(payload)
        except FileNotFoundError as exc:
            raise AttachmentError("attachment_asset_missing", "附件原件不存在。") from exc

    def verify(self, asset_id: str) -> Path:
        ref = self.metadata(asset_id)
        blob = self._blob_path(asset_id)
        if not blob.is_file() or blob.stat().st_size != ref.size_bytes:
            raise AttachmentError("attachment_integrity_failed", "附件原件不完整。")
        return blob

    def open(self, asset_id: str) -> BinaryIO:
        return self.verify(asset_id).open("rb")

    def _draft_path(self, draft_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", draft_id):
            raise AttachmentError("attachment_draft_expired", "附件草稿标识无效。")
        return self.drafts_dir / f"{draft_id}.json"

    def _blob_path(self, asset_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", asset_id):
            raise AttachmentError("attachment_asset_missing", "附件标识无效。")
        return self.blobs_dir / asset_id[:2] / asset_id

    def _metadata_path(self, asset_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", asset_id):
            raise AttachmentError("attachment_asset_missing", "附件标识无效。")
        return self.metadata_dir / f"{asset_id}.json"

    @staticmethod
    def _write_json_atomic(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

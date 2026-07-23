from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agent import AttachmentError, AttachmentStore


async def _chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


def test_attachment_store_streams_deduplicates_and_consumes_draft(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")

    first_id, first = asyncio.run(
        store.import_stream(
            _chunks(b"\x89PNG\r\n\x1a\n", b"payload"),
            display_name="screen.png",
            declared_media_type="image/png",
        )
    )
    second_id, second = asyncio.run(
        store.import_stream(
            _chunks(b"\x89PNG\r\n\x1a\n", b"payload"),
            display_name="copy.png",
            declared_media_type="image/png",
        )
    )

    assert first.asset_id == second.asset_id
    assert first_id != second_id
    assert store.consume_draft(first_id) == first
    assert store.consume_draft(first_id) == first
    assert store.verify(second.asset_id).read_bytes() == b"\x89PNG\r\n\x1a\npayload"


def test_attachment_store_rejects_declared_type_mismatch_and_cleans_temp(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")

    with pytest.raises(AttachmentError) as error:
        asyncio.run(
            store.import_stream(
                _chunks(b"%PDF-1.7\nbody"),
                display_name="fake.png",
                declared_media_type="image/png",
            )
        )

    assert error.value.code == "attachment_type_mismatch"
    assert list(store.tmp_dir.iterdir()) == []


def test_attachment_store_enforces_streaming_size_limit(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments", max_attachment_bytes=8)

    with pytest.raises(AttachmentError) as error:
        asyncio.run(
            store.import_stream(
                _chunks(b"1234", b"56789"),
                display_name="large.txt",
                declared_media_type="text/plain",
            )
        )

    assert error.value.code == "attachment_file_too_large"
    assert list(store.tmp_dir.iterdir()) == []


def test_attachment_store_validates_entire_text_and_filename_extension(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")

    with pytest.raises(AttachmentError) as binary_error:
        asyncio.run(
            store.import_stream(
                _chunks(b"a" * 9000, b"\xff"),
                display_name="binary.txt",
                declared_media_type="application/octet-stream",
            )
        )
    with pytest.raises(AttachmentError) as extension_error:
        asyncio.run(
            store.import_stream(
                _chunks(b"%PDF-1.7\nbody"),
                display_name="misleading.png",
                declared_media_type="application/octet-stream",
            )
        )

    assert binary_error.value.code == "attachment_type_unsupported"
    assert extension_error.value.code == "attachment_type_mismatch"


def test_attachment_store_expires_draft_lease(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments", draft_ttl_seconds=-1)
    draft_id, _ = asyncio.run(
        store.import_stream(
            _chunks(b"plain text"),
            display_name="note.txt",
            declared_media_type="text/plain",
        )
    )

    with pytest.raises(AttachmentError) as error:
        store.draft(draft_id)

    assert error.value.code == "attachment_draft_expired"


def test_attachment_store_gc_preserves_references_and_removes_old_orphans(
    tmp_path: Path,
) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    first_draft, referenced = asyncio.run(
        store.import_stream(
            _chunks(b"first text"),
            display_name="first.txt",
            declared_media_type="text/plain",
        )
    )
    second_draft, orphan = asyncio.run(
        store.import_stream(
            _chunks(b"second text"),
            display_name="second.txt",
            declared_media_type="text/plain",
        )
    )
    reconcile_at = store.draft_expires_at(second_draft) + 1
    store.consume_draft(first_draft)
    store.consume_draft(second_draft)
    for asset_id in (referenced.asset_id, orphan.asset_id):
        os.utime(store.metadata_dir / f"{asset_id}.json", (1, 1))
        os.utime(store.verify(asset_id), (1, 1))
    cache = store.root / "provider-cache" / "entry.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"_asset_id": orphan.asset_id}), encoding="utf-8")

    result = store.reconcile({referenced.asset_id}, now=reconcile_at, grace_seconds=1)

    assert result["removed_assets"] == 1
    assert store.verify(referenced.asset_id).is_file()
    with pytest.raises(AttachmentError):
        store.metadata(orphan.asset_id)
    assert not cache.exists()

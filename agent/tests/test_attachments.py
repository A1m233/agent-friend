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


@pytest.mark.parametrize(
    ("name", "data", "media"),
    [
        ("fake.png", b"%PDF-1.7\nbody", "application/pdf"),
        ("photo.png", b"\xff\xd8\xffpayload", "image/jpeg"),
        ("binary.dat", b"\x00\xff", "application/octet-stream"),
        ("empty.txt", b"", "text/plain"),
        (
            "report.docx",
            b"PK\x03\x04binary",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("animation.gif", b"GIF89a\x00", "image/gif"),
    ],
)
def test_store_accepts_files_without_model_or_parser_requirements(
    tmp_path: Path, name: str, data: bytes, media: str
) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    draft, ref = asyncio.run(
        store.import_stream(_chunks(data), display_name=name, declared_media_type="image/png")
    )
    assert store.consume_draft(draft) == ref
    assert ref.display_name == name and ref.media_type == media
    assert store.verify(ref.asset_id).read_bytes() == data
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


def test_non_utf8_text_is_stored_without_decoding(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    data = b"a" * 9000 + b"\xff"
    _, ref = asyncio.run(store.import_stream(_chunks(data), display_name="binary.txt"))
    assert store.verify(ref.asset_id).read_bytes() == data


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

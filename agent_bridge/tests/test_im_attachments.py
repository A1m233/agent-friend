from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from agent.attachments import AttachmentStore
from agent_bridge.protocols.im.attachments import IMAttachmentResolver
from agent_bridge.protocols.im.event import InboundEvent
from qqbot_agent_sdk.dto import MessageAttachment


def _event(*, content: str = "", attachments: list[MessageAttachment]) -> InboundEvent:
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id="OPENID-USER",
        user_id="OPENID-USER",
        chat_scope="c2c",
        content=content,
        message_id="MSG-ATTACHMENT-1",
        timestamp="2026-07-23T10:00:00Z",
        message_type=0,
        attachments=attachments,
        msg_elements=[],
    )


@pytest.mark.asyncio
async def test_resolver_imports_image_for_attachment_only_message(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://cdn.example.test/photo.png"
        return httpx.Response(
            200, content=b"\x89PNG\r\n\x1a\ncontent", headers={"content-type": "image/png"}
        )

    resolver = IMAttachmentResolver(
        AttachmentStore(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve(
        "qq",
        _event(
            attachments=[
                MessageAttachment(
                    url="https://cdn.example.test/photo.png",
                    filename="photo.png",
                    content_type="image/png",
                )
            ]
        ),
    )

    assert result.action == "route"
    assert len(result.attachments) == 1
    assert result.attachments[0].media_type == "image/png"
    assert result.attachments[0].display_name == "photo.png"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "sdk_content_type", "response_content_type"),
    [
        ("photo.jpg", "image/png", "image/png"),
        ("photo.png", "image/jpeg", "image/jpeg"),
        ("photo.bin", "", "application/octet-stream"),
    ],
)
async def test_resolver_accepts_qq_image_metadata_mismatch_using_detected_type(
    tmp_path: Path,
    filename: str,
    sdk_content_type: str,
    response_content_type: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\ncontent",
            headers={"content-type": response_content_type},
        )

    resolver = IMAttachmentResolver(
        AttachmentStore(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve(
        "qq",
        _event(
            attachments=[
                MessageAttachment(
                    url="https://cdn.example.test/media",
                    filename=filename,
                    content_type=sdk_content_type,
                )
            ]
        ),
    )

    assert result.action == "route"
    assert len(result.attachments) == 1
    assert result.attachments[0].media_type == "image/png"
    assert result.attachments[0].display_name == "photo.png"


@pytest.mark.asyncio
async def test_resolver_still_rejects_unknown_attachment_bytes(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x00not-a-supported-image",
            headers={"content-type": "image/png"},
        )

    resolver = IMAttachmentResolver(
        AttachmentStore(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve(
        "qq",
        _event(
            attachments=[
                MessageAttachment(
                    url="https://cdn.example.test/media",
                    filename="photo.png",
                    content_type="image/png",
                )
            ]
        ),
    )

    assert result.action == "reply"
    assert result.reason == "attachment_import_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "filename", "content_type", "content", "expected_media_type"),
    [
        (
            "https://cdn.example.test/report.pdf",
            "report.pdf",
            "application/pdf",
            b"%PDF-1.7\ncontent",
            "application/pdf",
        ),
        (
            "https://cdn.example.test/notes.txt",
            "notes.txt",
            "text/plain",
            "hello 附件".encode(),
            "text/plain",
        ),
    ],
)
async def test_resolver_imports_supported_documents(
    tmp_path: Path,
    url: str,
    filename: str,
    content_type: str,
    content: bytes,
    expected_media_type: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": content_type})

    resolver = IMAttachmentResolver(
        AttachmentStore(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve(
        "qq",
        _event(
            attachments=[MessageAttachment(url=url, filename=filename, content_type=content_type)]
        ),
    )

    assert result.action == "route"
    assert result.attachments[0].media_type == expected_media_type


@pytest.mark.asyncio
async def test_resolver_directly_replies_when_download_fails(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    resolver = IMAttachmentResolver(
        AttachmentStore(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve(
        "qq",
        _event(attachments=[MessageAttachment(url="https://cdn.example.test/report.pdf")]),
    )

    assert result.action == "reply"
    assert result.reason == "attachment_import_failed"
    assert result.reply is not None
    assert "重新发送" in result.reply.text


@pytest.mark.asyncio
async def test_resolver_keeps_voice_out_of_general_attachment_pipeline(tmp_path: Path) -> None:
    resolver = IMAttachmentResolver(AttachmentStore(tmp_path))
    event = _event(
        attachments=[MessageAttachment(content_type="audio/wav", voice_wav_url="https://x/a.wav")]
    )

    result = await resolver.resolve("qq", event)

    assert result.action == "route"
    assert result.attachments == ()


@pytest.mark.asyncio
async def test_resolver_rejects_private_download_target(tmp_path: Path) -> None:
    resolver = IMAttachmentResolver(AttachmentStore(tmp_path))
    result = await resolver.resolve(
        "qq",
        _event(attachments=[MessageAttachment(url="http://127.0.0.1/file.pdf")]),
    )

    assert result.action == "reply"
    assert result.reason == "attachment_import_failed"

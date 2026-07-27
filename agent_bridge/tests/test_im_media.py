from __future__ import annotations

import pytest
from agent_bridge.protocols.im.event import InboundEvent
from agent_bridge.protocols.im.media import (
    InboundMedia,
    QQFaceResolver,
    render_model_media_context,
    render_user_media_notice,
)
from qqbot_agent_sdk.dto import MessageAttachment


def _event(
    content: str,
    *,
    attachments: list[MessageAttachment] | None = None,
) -> InboundEvent:
    return InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_id="OPENID-USER",
        user_id="OPENID-USER",
        chat_scope="c2c",
        content=content,
        message_id="MSG-MEDIA-1",
        timestamp="2026-07-28T01:00:00Z",
        message_type=0,
        attachments=attachments or [],
    )


@pytest.mark.asyncio
async def test_qq_face_with_image_attachment_removes_wire_placeholder() -> None:
    attachment = MessageAttachment(
        url="https://cdn.example.test/sticker.jpg",
        filename="sticker.jpg",
        content_type="image/jpeg",
    )
    result = await QQFaceResolver().resolve(
        "qq",
        _event(
            '<faceType=6,faceId="0",ext="eyJ0ZXh0IjoiIn0=">',
            attachments=[attachment],
        ),
    )

    assert result.action == "route"
    assert result.event is not None
    assert result.event.content == ""
    assert result.media == ()


@pytest.mark.asyncio
async def test_qq_face_without_attachment_preserves_semantic_only_fidelity() -> None:
    result = await QQFaceResolver().resolve(
        "qq",
        _event('<faceType=4,faceId="",ext="eyJ0ZXh0IjoiW+WWguWWgl0ifQ==">'),
    )

    assert result.action == "route"
    assert result.event is not None
    assert result.event.content == "[喂喂]"
    assert result.media[0].kind == "platform_face"
    assert result.media[0].fidelity == "semantic_only"
    assert result.media[0].semantic_label == "[喂喂]"


@pytest.mark.asyncio
async def test_qq_face_resolver_preserves_non_face_content() -> None:
    event = _event("<用户输入的普通尖括号文本>")
    result = await QQFaceResolver().resolve("qq", event)

    assert result.event is event
    assert result.media == ()


def test_media_feedback_states_actual_read_boundary() -> None:
    media = (
        InboundMedia(
            source="qq_builtin_face",
            kind="platform_face",
            fidelity="semantic_only",
            semantic_label="[捂脸]",
            reason="qq_face_visual_unavailable",
        ),
        InboundMedia(
            source="im_attachment",
            kind="animated_image",
            fidelity="unsupported",
            reason="media_gif_unsupported",
            display_name="sticker.gif",
        ),
    )

    model_context = render_model_media_context(media)
    assert model_context is not None
    assert '"fidelity": "semantic_only"' in model_context
    assert "不得声称看到了未提供的图像" in model_context
    notice = render_user_media_notice(media)
    assert "GIF 我暂时看不了" in notice
    assert "QQ 只告诉我这是 [捂脸]" in notice

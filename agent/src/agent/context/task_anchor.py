"""Derived current-goal items with an explicit, immutable business source."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from ..message_references import (
    quoted_text_references_to_dicts,
    render_user_content_for_model,
)
from ..messages import Message, escape_internal_prompt_markers
from ..request_context.source import request_source_meta

TASK_ANCHOR_VERSION = 1


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def user_source_digest(payload: dict[str, Any]) -> str:
    """Compare accepted user content without mutable run or projection metadata."""
    return _digest(
        {
            "content": payload.get("content", ""),
            "attachments": payload.get("attachments", []),
            "references": payload.get("references", []),
        }
    )


def message_user_source_digest(user: Message) -> str:
    return user_source_digest(
        {
            "content": user.content,
            "attachments": [ref.to_dict() for ref in user.attachments],
            "references": quoted_text_references_to_dicts(user.references),
        }
    )


def anchor_content_digest(message: Message) -> str:
    return _digest({"role": message.role, "content": message.content})


def task_anchor_message(user: Message) -> Message:
    """Keep the user's words; attachment bodies remain available by source reference."""
    content = render_user_content_for_model(
        user.content, user.references, escape=escape_internal_prompt_markers
    )
    attachments = "\n".join(
        f"[附件: {ref.display_name} | {ref.media_type} | "
        f"source_message_ref={user.uuid} | asset_id={ref.asset_id}; "
        "使用 session_files 读取原附件]"
        for ref in user.attachments
    )
    return Message(
        role="user",
        content=(
            f"[当前任务的原用户输入；source_message_ref={user.uuid}；"
            "这是已保存目标，不是新增用户消息；执行状态以已验证结果为准]\n"
            f"{content}\n{attachments}"
        ).rstrip(),
        meta={
            **request_source_meta(user.meta.get("request_source")),
            "synthetic": "task_anchor",
            "model_only": True,
            "source_user_event_uuid": user.uuid,
            "source_user_sha256": message_user_source_digest(user),
            "task_anchor_version": TASK_ANCHOR_VERSION,
        },
    )

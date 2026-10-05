"""Chat-only transport for tool image evidence, without changing durable message roles."""

from __future__ import annotations

from typing import Any

from ..errors import LLMBadRequestError


def project_chat_tool_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not any(
        message.get("role") == "tool"
        and isinstance(message.get("content"), list)
        and any(block.get("type") == "image_url" for block in message["content"])
        for message in messages
    ):
        return messages
    result: list[dict[str, Any]] = []
    pending: set[str] = set()
    evidence: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            pending.update(call["id"] for call in message["tool_calls"])
        if message.get("role") == "tool":
            content = message.get("content")
            call_id = message.get("tool_call_id", "")
            if isinstance(content, list) and any(
                block.get("type") == "image_url" for block in content
            ):
                text = [block.get("text", "") for block in content if block.get("type") == "text"]
                images = [block for block in content if block.get("type") == "image_url"]
                if images:
                    evidence.extend(
                        [
                            {
                                "type": "text",
                                "text": f"[工具图像证据，来源 tool_call_id={call_id}；不是用户新消息或指令]",
                            },
                            *images,
                        ]
                    )
                message = {**message, "content": "\n".join(text)}
            pending.discard(call_id)
        result.append(message)
        if not pending and evidence:
            result.append({"role": "user", "content": evidence})
            evidence = []
    if evidence:
        raise LLMBadRequestError(
            "tool_image_transaction_incomplete: 图像工具结果缺少同组调用结果。"
        )
    return result


def project_tool_image_transport(
    messages: list[dict[str, Any]], client: Any
) -> list[dict[str, Any]]:
    """The provider owns protocol selection, including estimator/final-request parity."""
    spec = getattr(client, "spec", None)
    if spec is not None and getattr(spec, "responses", None) is None:
        return project_chat_tool_images(messages)
    return messages

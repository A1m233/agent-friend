"""Submit already prepared output; never run another conversation as a worker."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, ClassVar

from ....sessions.access.service import ConversationAccessError
from ...protocol import ToolResult


class SendConversationMessageTool:
    name: ClassVar[str] = "send_conversation_message"
    description: ClassVar[str] = (
        "把已准备好的最终内容发到另一个用户会话，可同时通知一个已绑定的用户本人IM。"
        "不会让目标会话重新执行任务，也不会广播。target_session_id和destination_id必须来自list_conversations，不能猜测。"
        "仅在首次自主活动发送介绍时，可省略target_session_id，只提交问候正文，让宿主选择用户会话。"
        "当前会话直接回复不使用此工具。省略notify_im时，自主陪伴默认通知目标会话已绑定的用户本人QQ，普通会话仅写入会话。"
        "明确只投递会话用false；显式通知用true并指定该会话已发现的destination_id。"
        "返回queued只代表已接受，不代表用户已收到；不要为等待结果反复发送。"
        "联系暂停或去向失效时停止或重新发现，不绕过暂停换通道。"
        "仅发送正文，不支持附带图片或文件。"
        "转述已读取的会话消息时，可用source_messages关联原消息；引用来自read_conversation，独立见闻可省略。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["content"],
        "properties": {
            "source_messages": {
                "type": "array",
                "maxItems": 8,
                "description": "本次分享依据的原消息，不是投递目标；使用实际读取结果中的精确会话与消息ID。",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["session_id", "message_id"],
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "minLength": 1,
                            "description": "read_conversation结果的session_id。",
                        },
                        "message_id": {
                            "type": "string",
                            "minLength": 1,
                            "description": "该读取结果中的message_id，不是运行ID。",
                        },
                    },
                },
            },
            "target_session_id": {
                "type": "string",
                "minLength": 1,
                "description": "list_conversations返回的目标session_id，不是标题或IM地址。",
            },
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20000,
                "description": (
                    "已经完成的最终消息正文；不能填要求目标Agent继续执行的指令。"
                    "需要展示图片时，在正文中使用 Markdown 图片语法 ![图片说明](图片地址)。"
                    "图片地址须可被接收端直接访问；本地路径、asset_id 和 file_ref 不会自动转换为可见图片。"
                    "QQ 等 IM 端不保证显示 Markdown 图片，不要把消息发送成功当作图片已展示。"
                ),
            },
            "notify_im": {
                "type": "boolean",
                "description": "自主分享通常省略，自动选目标会话的本人QQ；用户明确要求仅在桌面接收时填false。",
            },
            "destination_id": {
                "type": "string",
                "minLength": 1,
                "description": "目标会话im_destinations中的精确destination_id，仅notify_im=true时填写。",
            },
        },
        "dependencies": {
            "source_messages": ["target_session_id"],
            "destination_id": ["target_session_id"],
        },
        "oneOf": [
            {
                "properties": {"notify_im": {"const": True}},
                "required": ["notify_im", "destination_id"],
            },
            {
                "properties": {"notify_im": {"const": False}},
                "not": {"required": ["destination_id"]},
            },
        ],
    }

    def __init__(self) -> None:
        self.submit: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        if self.submit is None:
            return ToolResult(
                '{"error":"delivery_unavailable","message":"当前入口尚未提供跨会话投递。"}',
                is_error=True,
            )
        try:
            return ToolResult(json.dumps(self.submit(args), ensure_ascii=False))
        except ConversationAccessError as exc:
            return ToolResult(
                json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False),
                is_error=True,
            )
        except ValueError:
            return ToolResult(
                '{"error":"delivery_conflict","message":"同一操作的消息内容发生冲突，请检查原投递结果，不要原样重试。"}',
                is_error=True,
            )

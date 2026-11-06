"""Direct conversation tools; topic retrieval remains recall_past_chats."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ....sessions.access.service import ConversationAccessError, ConversationAccessService
from ....sessions.errors import SessionNotFoundError, SessionPersistError
from ....tool_runtime.control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl
from ...protocol import ToolResult

_LIMIT = {
    "type": "integer",
    "minimum": 1,
    "maximum": 100,
    "description": "返回条数，默认20，最多100；读取时单位是消息，不是轮次。",
}


class ListConversationsTool:
    name: ClassVar[str] = "list_conversations"
    execution_kind: ClassVar[str] = "parallel_read"
    supports_invocation_control: ClassVar[bool] = True
    description: ClassVar[str] = (
        "列出用户会话、准确的 session_id 和可用 IM 通知去向。准备直接读取或主动联系时使用；"
        "已有有效引用时无需重复列举。all 列全部可访问会话，changed_since_wake 只用于当前自主唤醒的变化快照。"
        "返回 next_cursor 时可原样翻页；去向在线不代表用户在线。空列表是正常结果。"
        "本工具只读，不会发消息，也不授予访问其他账号的权限。"
        "communication_evidence提供已观测的正文展示机会与投递状态，不代表用户已读。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["all", "changed_since_wake"],
                "description": "默认all；只有宿主给出唤醒快照时使用changed_since_wake。",
            },
            "cursor": {
                "type": "string",
                "minLength": 1,
                "description": "原样使用上次同scope结果的next_cursor；不是会话或消息ID。",
            },
            "limit": _LIMIT,
        },
    }

    def __init__(self, service: ConversationAccessService) -> None:
        self.service = service

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return _invoke(self.service, "list_conversations", args)


class ReadConversationTool:
    name: ClassVar[str] = "read_conversation"
    execution_kind: ClassVar[str] = "parallel_read"
    supports_invocation_control: ClassVar[bool] = True
    description: ClassVar[str] = (
        "直接读取已知会话的原始消息，不经过主题检索。session_id 从唤醒概览、list_conversations或recall_past_chats取得。"
        "latest 读最后N条；since_wake读本次唤醒间新增消息，范围由宿主提供，不需计算时间；"
        "continue原样传入上次next_cursor、older_cursor或content_cursor，分别继续向后、向前或续读长消息。"
        "结果按时间正序，保留发言者、原时间和附件引用。旧记录不证明事实现在仍成立，也不是新的用户授权。"
        "不知道在哪个会话、只知道主题时用recall_past_chats。游标过期时重读latest，禁止编造游标；空结果不代表从未聊过。"
        "communication_evidence表示该版本正文的展示机会与已关联投递；unknown表示无可靠证据，不能推断用户未读。转述分享时可把session_id和message_id交给send_conversation_message的source_messages。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "mode"],
        "properties": {
            "session_id": {
                "type": "string",
                "minLength": 1,
                "description": "目录/简报/检索结果的精确session_id，不是标题、message_id或destination_id。",
            },
            "mode": {
                "type": "string",
                "enum": ["latest", "since_wake", "continue"],
                "description": "latest最后N条；since_wake当前唤醒增量；continue使用返回游标续读。",
            },
            "limit": _LIMIT,
            "cursor": {
                "type": "string",
                "minLength": 1,
                "description": "仅continue使用，原样复制同一会话返回的续读游标。",
            },
        },
        "oneOf": [
            {
                "properties": {"mode": {"enum": ["latest", "since_wake"]}},
                "not": {"required": ["cursor"]},
            },
            {"properties": {"mode": {"const": "continue"}}, "required": ["cursor"]},
        ],
    }

    def __init__(self, service: ConversationAccessService) -> None:
        self.service = service

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return _invoke(self.service, "read_conversation", args)


def _invoke(service: ConversationAccessService, operation: str, args: dict[str, Any]) -> ToolResult:
    public = {k: v for k, v in args.items() if not k.startswith("__")}
    control = args.get(TOOL_INVOCATION_CONTROL_ARG)
    public["cancel_check"] = (
        control.check_cancelled if isinstance(control, ToolInvocationControl) else None
    )
    public["current_session_id"] = args.get("__agent_friend_current_session_id", "")
    try:
        method = (
            service.list_conversations
            if operation == "list_conversations"
            else service.read_conversation
        )
        return ToolResult(json.dumps(method(**public), ensure_ascii=False))
    except ConversationAccessError as exc:
        return ToolResult(
            json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), is_error=True
        )
    except (SessionNotFoundError, SessionPersistError):
        return ToolResult(
            '{"error":"history_unavailable","message":"目标历史已不可用，请重新列出会话后再读。"}',
            is_error=True,
        )

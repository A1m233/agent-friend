"""Owner-local, read-only view of one autonomous activity at a time."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any, ClassVar

from ....sessions.access.service import ConversationAccessError, ConversationAccessService
from ....sessions.errors import SessionError
from ....sessions.query import CancelCheck
from ....sessions.read_views.journal import HistoryWindow
from ....tool_runtime.control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl
from ...protocol import ToolResult


class ReadInnerActivityTool:
    name: ClassVar[str] = "read_inner_activity"
    execution_kind: ClassVar[str] = "parallel_read"
    supports_invocation_control: ClassVar[bool] = True
    description: ClassVar[str] = (
        "只读回看自己的内心活动。无参数读最近一次唤醒，无需会话ID。用older_cursor向前翻一轮，"
        "用content_cursor续读本轮长正文，两者均原样传给cursor。按主题检索后，将命中的"
        "inner_activity_ref（或内心记录的单条source_refs）原样传给source_ref定位该轮。范围引用从末尾的唤醒读起。"
        "正文是过去的活动记录；deliveries 为 null 表示缺少投递依据，空列表表示没有查到普通会话回执。IM 发送状态无法证明用户已读。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cursor": {
                "type": "string",
                "minLength": 1,
                "description": "本工具上次返回的翻页或续读游标。",
            },
            "source_ref": {
                "type": "string",
                "minLength": 1,
                "description": "检索命中的内心活动引用，与 cursor 二选一。",
            },
        },
        "not": {"required": ["cursor", "source_ref"]},
    }

    def __init__(self, access: ConversationAccessService) -> None:
        self.access = access
        self.receipts: Callable[[str, str], list[dict[str, Any]]] | None = None
        self.inner_id: Callable[[], str | None] = lambda: None

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        control = args.get(TOOL_INVOCATION_CONTROL_ARG)
        check = control.check_cancelled if isinstance(control, ToolInvocationControl) else None
        try:
            result = self._read(args, check)
        except ConversationAccessError as error:
            return ToolResult(
                json.dumps({"error": error.code, "message": str(error)}, ensure_ascii=False),
                is_error=True,
            )
        except (SessionError, OSError, sqlite3.Error):
            return ToolResult(
                '{"error":"history_unavailable","message":"活动记录暂时无法读取。"}', is_error=True
            )
        return ToolResult(json.dumps(result, ensure_ascii=False))

    def _read(self, args: dict[str, Any], check: CancelCheck) -> dict[str, Any]:
        sid = self.inner_id() or self.access.existing_inner_id()
        if sid is None:
            if args.get("cursor") or args.get("source_ref"):
                raise ConversationAccessError("history_unavailable", "引用对应的内心活动已不可用。")
            return {"activity": None, "older_cursor": None}
        state: dict[str, Any] = {}
        if cursor := args.get("cursor"):
            state = self.access.decode_cursor(cursor, kind=self.name, session_id=sid)
        if ref := args.get("source_ref"):
            source, _, anchor = ref.partition("#")
            if source != sid or not anchor:
                raise ConversationAccessError(
                    "invalid_source",
                    "需要内心活动的原始source_ref；普通会话请用read_conversation。",
                )
            endpoints = anchor.split("..")
            if len(endpoints) > 2 or not all(endpoints):
                raise ConversationAccessError("invalid_source", "请使用完整的活动引用。")
            state["anchor"] = endpoints[-1]
        reader = getattr(self.access.store, "history_window", None)
        if not callable(reader):
            raise ConversationAccessError("read_unavailable", "当前存储不支持读取活动历史。")
        try:
            page: HistoryWindow = reader(
                sid,
                turn_limit=1,
                before=state.get("before"),
                history_epoch=state.get("epoch"),
                anchor_event_uuid=state.get("anchor"),
                cancel_check=check,
            )
        except KeyError as exc:
            raise ConversationAccessError("source_missing", "引用已失效，请重新检索。") from exc
        except ValueError as exc:
            raise ConversationAccessError(
                "history_changed", "历史已变化，请从最近活动或原始引用重新读取。"
            ) from exc
        if not page.read_only:
            raise ConversationAccessError("invalid_source", "目标不是内心活动。")
        events = page.active_events
        trigger = next((e for e in events if e.type == "system_trigger"), None)
        if trigger is None:
            return {"activity": None, "older_cursor": None}
        if state.get("wake", trigger.uuid) != trigger.uuid:
            raise ConversationAccessError("history_changed", "最近活动已变化，请重新读取。")
        body = "\n\n".join(
            e.payload["content"]
            for e in events
            if e.type == "assistant_message"
            and not e.payload.get("partial")
            and isinstance(e.payload.get("content"), str)
        )
        offset = state.get("offset", 0)
        end = offset + 4000
        base = {"kind": self.name, "session_id": sid, "epoch": page.history_epoch}
        run_id = trigger.meta.get("run_id") or next(
            (e.payload.get("run_id") for e in events if e.type == "run_terminal"), None
        )
        return {
            "activity": {
                "started_at": trigger.ts.isoformat(),
                "text": body[offset:end],
                "source": "inner_world",
                "deliveries": self.receipts(sid, run_id) if self.receipts and run_id else None,
            },
            "older_cursor": self.access.encode_cursor({**base, "before": page.history_cursor})
            if page.history_cursor
            else None,
            "content_cursor": self.access.encode_cursor(
                {**state, **base, "offset": end, "wake": trigger.uuid}
            )
            if end < len(body)
            else None,
        }

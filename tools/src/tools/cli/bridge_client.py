"""``agent-cli --bridge`` 模式下的 HTTP 客户端。

把 :class:`agent_bridge` 的两类出口收成一个 client：

- **meta REST**（``GET/POST /v1/...``）—— 列表 / 单条 / 切换 persona / 切换 model
- **AG-UI run**（``POST /ag-ui/run``）—— SSE 流式跑一轮对话

``run`` 把 AG-UI SSE 事件反解回内部 :class:`agent.ConversationEvent`（duck-type），
让 CLI 主循环 ``for ev in stream:`` 那段代码原样复用——CLI 渲染层不需要感知
"我在跟 in-process Conversation 说话还是跟远程 bridge 说话"。

设计要点（详见 docs/requirements/006-agent-bridge/design.md §4.7）：

- 自写 SSE parser，不依赖 ``ag-ui-protocol`` 官方 Python SDK——CLI 只消费有限
  几类事件，自写 ≤ 200 行可控可测
- ``RUN_ERROR`` 一律抛 :class:`BridgeRunError`，让 CLI 主循环走"统一 LLMError
  分支"——bridge 给的 ``message`` 已经是拟人化文案，CLI 直接展示
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from agent import (
    ConversationEvent,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeSessionSummary:
    """``GET /v1/sessions`` 返回的一行。

    跟 :class:`agent.SessionSummary` 字段大体对齐，但是 wire dict（不是
    dataclass），故 ``created_at`` / ``updated_at`` 保留 ISO 字符串形态。
    """

    session_id: str
    title: str
    persona: str
    model: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BridgePersonaInfo:
    """``GET /v1/personas`` 返回的一行。"""

    id: str
    name: str
    source: str
    description: str | None


class BridgeError(Exception):
    """bridge HTTP / 协议级失败的统一基类。"""


class BridgeRunError(BridgeError):
    """AG-UI run 收到 ``RUN_ERROR`` 事件。

    Attributes:
        code: ``RUN_ERROR.code``（如 ``rate_limit`` / ``upstream_transient``）。
        message: ``RUN_ERROR.message``（已是拟人化文案，可直接展示）。
    """

    def __init__(self, message: str, code: str | None) -> None:
        super().__init__(message)
        self.code = code


class BridgeClient:
    """``agent-cli --bridge URL`` 模式下的远程 bridge 客户端。

    线程模型：一个 CLI 进程一个 :class:`BridgeClient`，不并发使用。内部 httpx
    client 复用一个连接池；``close()`` 在 CLI 主循环退出时调用。
    """

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        """
        Args:
            base_url: bridge 地址，如 ``http://127.0.0.1:18800``。末尾的 ``/``
                会被剥掉。
            timeout: 单次 HTTP 请求的总超时（秒）；AG-UI run 是流式，长连接
                不受这个 timeout 控制——传给 httpx 时只用作 connect / read
                空闲超时。
        """
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BridgeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------- meta REST ----------

    def list_sessions(self) -> list[BridgeSessionSummary]:
        """``GET /v1/sessions``。"""
        resp = self._client.get("/v1/sessions")
        resp.raise_for_status()
        return [_summary_from_dict(item) for item in resp.json()]

    def get_session_events(self, session_id: str) -> dict[str, Any]:
        """``GET /v1/sessions/{id}``，返回原始 dict（含 ``events`` 列表）。

        Raises:
            httpx.HTTPStatusError: 404 等。
        """
        resp = self._client.get(f"/v1/sessions/{session_id}")
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    def switch_persona(self, session_id: str, persona: str) -> dict[str, Any]:
        """``POST /v1/sessions/{id}/persona``。"""
        resp = self._client.post(
            f"/v1/sessions/{session_id}/persona",
            json={"persona": persona},
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    def switch_model(self, session_id: str, model: str) -> dict[str, Any]:
        """``POST /v1/sessions/{id}/model``。"""
        resp = self._client.post(
            f"/v1/sessions/{session_id}/model",
            json={"model": model},
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    def list_personas(self) -> list[BridgePersonaInfo]:
        """``GET /v1/personas``。"""
        resp = self._client.get("/v1/personas")
        resp.raise_for_status()
        return [_persona_from_dict(item) for item in resp.json()]

    # ---------- AG-UI run ----------

    def run(
        self,
        *,
        thread_id: str,
        user_input: str,
    ) -> Iterator[ConversationEvent]:
        """``POST /ag-ui/run`` 流式跑一轮，yield 出 :class:`ConversationEvent`。

        Args:
            thread_id: 远端 bridge 上的 session_id。bridge 端 auto-create
                语义：``thread_id`` 不存在时会自动建（design §4.4.4）。
            user_input: 本轮 user 消息文本。

        Yields:
            duck-type 的 :class:`ConversationEvent` —— CLI 主循环原样消费。

        Raises:
            BridgeRunError: 服务端返回了 ``RUN_ERROR`` 事件。
            httpx.HTTPStatusError: 装配阶段失败（非 ``RUN_STARTED`` 即返 4xx/5xx）。
        """
        payload = _build_run_agent_input(thread_id=thread_id, user_input=user_input)
        with self._client.stream(
            "POST",
            "/ag-ui/run",
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            yield from _decode_sse_to_conversation_events(resp.iter_lines())


# ---------- helpers: dict -> dataclass ----------


def _summary_from_dict(item: dict[str, Any]) -> BridgeSessionSummary:
    """meta REST ``/v1/sessions`` 单条 dict → :class:`BridgeSessionSummary`。

    服务端 :class:`agent.SessionSummary` 的 ``created_at`` / ``updated_at`` 是
    :class:`datetime.datetime`，被 FastAPI 默认编码为 ISO 字符串。本侧不再
    反解为 datetime——CLI 展示直接用字符串就行。
    """
    return BridgeSessionSummary(
        session_id=str(item.get("session_id", "")),
        title=str(item.get("title", "")),
        persona=str(item.get("persona", "")),
        model=str(item.get("model", "")),
        created_at=str(item.get("created_at", "")),
        updated_at=str(item.get("updated_at", "")),
    )


def _persona_from_dict(item: dict[str, Any]) -> BridgePersonaInfo:
    desc_raw = item.get("description")
    return BridgePersonaInfo(
        id=str(item.get("id", "")),
        name=str(item.get("name", "")),
        source=str(item.get("source", "")),
        description=str(desc_raw) if desc_raw is not None else None,
    )


# ---------- AG-UI request 构造 ----------


def _build_run_agent_input(*, thread_id: str, user_input: str) -> dict[str, Any]:
    """构造 ``RunAgentInput`` 的 wire JSON。

    AG-UI ``RunAgentInput`` 必填 ``threadId`` / ``runId`` / ``state`` /
    ``messages`` / ``tools`` / ``context`` / ``forwardedProps``。bridge 端实际
    只用到 ``threadId`` / ``runId`` / ``messages[-1].content``（其他字段保留
    协议形态但内部不消费，design §4.4.2）。

    Args:
        thread_id: 远端 session_id。
        user_input: 本轮 user 文本，包成单条 UserMessage 发过去。
    """
    return {
        "threadId": thread_id,
        "runId": uuid4().hex,
        "state": {},
        "messages": [
            {
                "id": uuid4().hex,
                "role": "user",
                "content": user_input,
            }
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


# ---------- AG-UI SSE 反解 ----------


def _decode_sse_to_conversation_events(
    lines: Iterator[str],
) -> Iterator[ConversationEvent]:
    """从原始 SSE 行序列反解出 :class:`ConversationEvent`。

    AG-UI 三段式 → 内部事件的映射（与 ``agent_bridge/protocols/ag_ui/encoders.py``
    的编码动作对偶）：

    - ``TEXT_MESSAGE_START`` → 仅记录 message_id 状态，不 yield
    - ``TEXT_MESSAGE_CONTENT`` → :class:`TextDelta`
    - ``TEXT_MESSAGE_END`` → 关闭当前文本 message，不 yield
    - ``TOOL_CALL_START`` → 缓存 tool_call_id / tool_name
    - ``TOOL_CALL_ARGS`` → 累积 args JSON 字符串到 buffer
    - ``TOOL_CALL_END`` → 解析 buffer，yield :class:`ToolCallRequest`
    - ``TOOL_CALL_RESULT`` → yield :class:`ToolCallResult`
        （``content`` 前缀 ``[error] `` 即 is_error=True，剥掉前缀写到 text）
    - ``RUN_FINISHED`` → yield :class:`TurnDone(stop_reason="end_turn")`
    - ``RUN_ERROR`` → 抛 :class:`BridgeRunError`
    - 其他事件（RUN_STARTED / STEP_*）→ 静默跳过
    """
    pending_args: dict[str, str] = {}
    pending_tool_name: dict[str, str] = {}

    for raw_line in lines:
        if not raw_line:
            continue
        if not raw_line.startswith("data:"):
            continue
        data_str = raw_line[len("data:") :].strip()
        if not data_str:
            continue
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            logger.warning("bridge SSE: 跳过无法解析的 data line: %r", data_str[:120])
            continue

        ev_type = event.get("type")
        if ev_type == "TEXT_MESSAGE_CONTENT":
            delta = event.get("delta", "")
            if delta:
                yield TextDelta(text=str(delta))

        elif ev_type == "TOOL_CALL_START":
            tcid = str(event.get("toolCallId", ""))
            pending_tool_name[tcid] = str(event.get("toolCallName", ""))
            pending_args[tcid] = ""

        elif ev_type == "TOOL_CALL_ARGS":
            tcid = str(event.get("toolCallId", ""))
            pending_args[tcid] = pending_args.get(tcid, "") + str(event.get("delta", ""))

        elif ev_type == "TOOL_CALL_END":
            tcid = str(event.get("toolCallId", ""))
            args_str = pending_args.pop(tcid, "")
            tool_name = pending_tool_name.pop(tcid, "")
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                logger.warning(
                    "bridge SSE: tool_call %s args JSON 解析失败: %r", tcid, args_str[:120]
                )
                args = {}
            yield ToolCallRequest(tool_call_id=tcid, tool_name=tool_name, args=args)

        elif ev_type == "TOOL_CALL_RESULT":
            content = str(event.get("content", ""))
            is_error = content.startswith("[error] ")
            text = content[len("[error] ") :] if is_error else content
            yield ToolCallResult(
                tool_call_id=str(event.get("toolCallId", "")),
                tool_name="",
                text=text,
                is_error=is_error,
                duration_seconds=0.0,
            )

        elif ev_type == "RUN_FINISHED":
            yield TurnDone(stop_reason="end_turn")
            return

        elif ev_type == "RUN_ERROR":
            raise BridgeRunError(
                message=str(event.get("message", "服务端返回错误，但未提供 message")),
                code=event.get("code"),
            )

        # 其他事件（RUN_STARTED / TEXT_MESSAGE_START/END / STEP_* 等）当前 CLI
        # 渲染层不需要——静默跳过，保留前向兼容（新事件类型不会让客户端崩）

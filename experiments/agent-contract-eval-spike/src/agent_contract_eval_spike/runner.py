"""跑单条 case → 拿到 :class:`Trace`。

每个 case 起一个**独立 tmp 目录 + JsonlSessionStore**:
- v1 case(无 fixture_session): tmp 目录初始为空,recall_past_chats 翻到 0 条
  fixture(只能扫到本轮 active session 自己写的 user_message),等价于以前的
  NullSessionStore 行为,但走真实 store 路径更接近 prod
- v2 case(有 fixture_session): 跑前往 tmp 目录写 fixture session(伪过去对话)的
  jsonl,recall_past_chats 能真召回到内容,judge 才有"agent 是否如实引用 / 是否
  fabricate"的判分依据

tmp 目录跟 case 一一对应,跑完自动清。
"""

from __future__ import annotations

import dataclasses
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Event,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    Session,
    SessionManager,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
    default_context_manager,
    make_default_registry,
)
from llm_providers import LLMClient, ProviderSpec

from .schema import Case, FixtureSession, ToolCallTrace, Trace


def _build_manager(store: JsonlSessionStore, model: str | None) -> SessionManager:
    """装配一个挂指定 store 的 SessionManager。每 case 一个,因为 store 是 per-case 的。"""

    def _llm_factory(_model: str) -> LLMClient:
        spec = ProviderSpec.from_env(prefix="DEEPSEEK")
        if model:
            spec = dataclasses.replace(spec, model=model)
        return LLMClient(spec)

    def _prompt_factory(pid: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid)

    return SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=default_context_manager,
        tool_registry=make_default_registry(session_store=store, stderr_warn=False),
    )


def _inject_fixture(
    store: JsonlSessionStore,
    fixture: FixtureSession,
    model: str,
) -> None:
    """把 fixture session 写入 store(作为一个独立的"过去 session" jsonl 文件)。"""
    now = datetime.now(UTC)
    session_ts = now - timedelta(days=fixture.days_ago)
    session = Session.new(
        title=f"fixture-{fixture.days_ago}d-ago",
        persona="default",
        model=model,
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        session_id=str(uuid4()),
        created_at=session_ts,
    )
    # 顺序往 session.events 里塞 user/assistant 事件;每条递增 1 分钟,保留时序
    for i, msg in enumerate(fixture.messages):
        ev_type = "user_message" if msg.role == "user" else "assistant_message"
        session.append(
            Event(
                type=ev_type,
                uuid=str(uuid4()),
                ts=session_ts + timedelta(minutes=i + 1),
                payload={"content": msg.text},
                meta={},
            )
        )
    store.create(session)


def run_case(case: Case, model: str) -> Trace:
    """跑一个 case,返回完整 Trace。挂了的话 trace.error 非空,Verdict 一律 fail。

    每个 case 内部:tmp_dir → JsonlSessionStore → (可选)注入 fixture → 跑 stream → 清 tmp。
    """
    try:
        with tempfile.TemporaryDirectory(prefix="agent-eval-") as tmp:
            store = JsonlSessionStore(Path(tmp))
            if case.fixture_session is not None:
                _inject_fixture(store, case.fixture_session, model)

            mgr = _build_manager(store, model)
            session = mgr.create(
                persona="default",
                model=model,
                persona_id=BUILTIN_DEFAULT_PERSONA_ID,
            )
            conv = mgr.start_conversation(session)

            tool_requests: dict[str, ToolCallRequest] = {}  # tool_call_id → request,等 Result 配对
            tool_calls: list[ToolCallTrace] = []
            text_buf: list[str] = []
            stop_reason = "unknown"

            for ev in conv.stream(case.query):
                if isinstance(ev, ToolCallRequest):
                    tool_requests[ev.tool_call_id] = ev
                elif isinstance(ev, ToolCallResult):
                    req = tool_requests.pop(ev.tool_call_id, None)
                    tool_calls.append(
                        ToolCallTrace(
                            name=ev.tool_name,
                            args=dict(req.args) if req else {},
                            result_text=ev.text,
                            is_error=ev.is_error,
                        )
                    )
                elif isinstance(ev, TextDelta):
                    text_buf.append(ev.text)
                elif isinstance(ev, TurnDone):
                    stop_reason = ev.stop_reason

            # 未配对的 ToolCallRequest(发了但没回 Result,极少)兜底落入 trace
            for req in tool_requests.values():
                tool_calls.append(
                    ToolCallTrace(
                        name=req.tool_name, args=dict(req.args), result_text="", is_error=False
                    )
                )

            return Trace(
                case_id=case.id,
                tool_calls=tool_calls,
                final_text="".join(text_buf),
                stop_reason=stop_reason,
            )
    except Exception as exc:  # noqa: BLE001 — PoC 故意宽 catch:跑挂了不阻断后续 case
        return Trace(
            case_id=case.id,
            tool_calls=[],
            final_text="",
            stop_reason="error",
            error=f"{type(exc).__name__}: {exc}",
        )

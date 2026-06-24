"""跑单条 case → 拿到 :class:`Trace`。

每个 case 起一个**全新 session**(NullSessionStore,不落盘),
跑一轮 ``conv.stream(case.query)``,收集 ``ToolCallRequest`` 与 text deltas。
"""

from __future__ import annotations

import dataclasses

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    MarkdownPromptBuilder,
    NullSessionStore,
    SessionManager,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
    default_context_manager,
    make_default_registry,
)
from llm_providers import LLMClient, ProviderSpec

from .schema import Case, ToolCallTrace, Trace


def build_session_manager(model: str | None = None) -> SessionManager:
    """装配一个 SessionManager,复用于所有 case(每个 case 仍跑独立 session)。"""

    def _llm_factory(_model: str) -> LLMClient:
        spec = ProviderSpec.from_env(prefix="DEEPSEEK")
        if model:
            spec = dataclasses.replace(spec, model=model)
        return LLMClient(spec)

    def _prompt_factory(pid: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid)

    store = NullSessionStore()
    return SessionManager(
        store=store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=default_context_manager,
        tool_registry=make_default_registry(session_store=store, stderr_warn=False),
    )


def run_case(case: Case, mgr: SessionManager, model: str) -> Trace:
    """跑一个 case,返回完整 Trace。挂了的话 trace.error 非空,Verdict 一律 fail。"""
    try:
        session = mgr.create(
            persona="default",
            model=model,
            persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        )
        conv = mgr.start_conversation(session)

        tool_requests: dict[str, ToolCallRequest] = {}  # 按 tool_call_id 暂存,等 Result 配对
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

        # 未配对的 ToolCallRequest(发了但没回 Result,极少出现)兜底落入 trace
        for req in tool_requests.values():
            tool_calls.append(
                ToolCallTrace(name=req.tool_name, args=dict(req.args), result_text="", is_error=False)
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

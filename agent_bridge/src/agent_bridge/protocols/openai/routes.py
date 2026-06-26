"""OpenAI ChatCompletion 路由层：把 HTTP 请求接进来、装配 :class:`Conversation`、
按 ``stream: true|false`` 分支调编码器。

错误模型（详见 docs/requirements/006-agent-bridge/design.md §4.6.2）：

- 解码失败 / 装配失败 → HTTP 4xx/5xx + OpenAI 标准 ``{"error": {...}}`` envelope
- 流式 ``conv.stream`` 中段抛错 → 在 SSE 流里 emit 一个 error chunk + ``[DONE]``
  （在 :func:`encode_streaming` 内部完成，路由层只负责装配阶段失败）

所有未识别异常统一走 :func:`agent_bridge.errors.map_exception` 兜底拟人化文案。
"""

from __future__ import annotations

import logging

from agent.latency import VoiceLatencyContext, log_voice_latency, monotonic_ms
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...assembly import BridgeRuntime
from ...errors import ProtocolError, build_openai_error_envelope, map_exception
from ...session_bridge import (
    PersistentBootstrap,
    SessionBridge,
    TransientBootstrap,
)
from .decoders import DecodeError, decode_chat_completion_request
from .encoders import encode_nonstreaming, encode_streaming

logger = logging.getLogger(__name__)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """把 ``POST /v1/chat/completions`` 挂到 :class:`FastAPI` 实例。

    路由函数是普通同步函数 —— :class:`agent.Conversation` 的 ``stream`` 是
    同步生成器（基于 :class:`llm_providers.LLMClient.stream`），FastAPI 在
    StreamingResponse 里能正确驱动同步迭代器。

    Args:
        app: 已建好的 :class:`FastAPI` 实例。
        runtime: 已装配好的 :class:`BridgeRuntime`。
    """
    bridge = SessionBridge(runtime)
    router = APIRouter()

    @router.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
        voice_latency = _voice_latency_context_from_headers(request)
        request_ms = monotonic_ms()
        log_voice_latency(logger, voice_latency, "agent_bridge_openai_inbound")
        try:
            body = await request.json()
        except ValueError as e:
            return _error_response(
                ProtocolError(
                    http_status=400,
                    code="bad_request",
                    message=f"非法 JSON body: {e}",
                    recoverable=False,
                )
            )

        try:
            decoded = decode_chat_completion_request(body)
        except DecodeError as e:
            return _error_response(
                ProtocolError(
                    http_status=400,
                    code="bad_request",
                    message=str(e),
                    recoverable=False,
                )
            )
        log_voice_latency(
            logger,
            voice_latency,
            "agent_bridge_openai_decoded",
            elapsed_ms=monotonic_ms() - request_ms,
            stream=decoded.stream,
        )

        model = decoded.model or runtime.default_model
        session_id_hint = request.headers.get("X-Agent-Friend-Session-Id")

        # 装配阶段失败在 200 之前——可以正常 4xx/5xx + envelope。stream 之后的
        # 错误由 encoder 在 SSE 流里 emit error chunk 处理。
        try:
            if session_id_hint:
                # 006 扩展位：显式指定已存在的 session_id，走持久化语义。
                # 客户端 request.messages 里的历史被忽略——session 自己的 jsonl 才是
                # 权威历史，open() 时会自动 replay。
                # 若 session 不存在，按 design.md §4.6.2 返回 404，不自动创建。
                if not bridge.session_exists(session_id_hint):
                    return _error_response(
                        ProtocolError(
                            http_status=404,
                            code="session_not_found",
                            message=f"session {session_id_hint} 不存在",
                            recoverable=False,
                        )
                    )
                conv = bridge.bind_persistent(
                    PersistentBootstrap(
                        thread_id=session_id_hint,
                        new_user_input=decoded.latest_user_input,
                        default_persona=runtime.default_persona,
                        default_model=model,
                    )
                )
            else:
                boot = TransientBootstrap(
                    history=decoded.history,
                    latest_user_input=decoded.latest_user_input,
                    persona=runtime.default_persona,
                    model=model,
                )
                conv = bridge.start_transient(boot)
            conv.set_voice_latency_context(
                voice_latency,
                short_reply_hint=_voice_short_reply_hint(runtime) if voice_latency else "",
                llm_overrides=_voice_llm_overrides(runtime) if voice_latency else None,
                disable_tools=runtime.settings.voice_latency_experiment_disable_tools,
            )
            _log_voice_experiments(runtime, voice_latency)
            log_voice_latency(
                logger,
                voice_latency,
                "agent_bridge_conversation_bound",
                session_id=conv.session.session_id,
                persistent=bool(session_id_hint),
                elapsed_ms=monotonic_ms() - request_ms,
            )
        except Exception as exc:
            logger.exception("OpenAI 装配 Conversation 失败")
            return _error_response(map_exception(exc))

        if decoded.stream:
            return StreamingResponse(
                encode_streaming(
                    conv,
                    decoded.latest_user_input,
                    model=model,
                    agent_runtime=runtime.agent_runtime,
                    voice_latency_context=voice_latency,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        try:
            payload = encode_nonstreaming(
                conv,
                decoded.latest_user_input,
                model=model,
                agent_runtime=runtime.agent_runtime,
            )
        except Exception as exc:
            logger.exception("OpenAI 非流式编码失败")
            return _error_response(map_exception(exc))
        return JSONResponse(content=payload)

    app.include_router(router)


def _error_response(err: ProtocolError) -> JSONResponse:
    """统一构造 OpenAI 错误响应（HTTP status + 标准 envelope）。"""
    return JSONResponse(
        status_code=err.http_status,
        content=build_openai_error_envelope(err),
    )


def _voice_latency_context_from_headers(request: Request) -> VoiceLatencyContext | None:
    trace_id = request.headers.get("X-Agent-Friend-Voice-Trace-Id")
    call_id = request.headers.get("X-Agent-Friend-Voice-Call-Id")
    round_seq_raw = request.headers.get("X-Agent-Friend-Voice-Round-Seq")
    if not trace_id or not call_id or not round_seq_raw:
        return None
    try:
        round_seq = int(round_seq_raw)
    except ValueError:
        return None
    return VoiceLatencyContext(trace_id=trace_id, call_id=call_id, round_seq=round_seq)


def _voice_short_reply_hint(runtime: BridgeRuntime) -> str:
    if not runtime.settings.voice_latency_experiment_short_reply:
        return ""
    return "语音通话低延迟实验：先用一句自然短句回应，再按需要补充重点；不要牺牲准确性。"


def _voice_llm_overrides(runtime: BridgeRuntime) -> dict[str, int]:
    max_tokens = runtime.settings.voice_latency_experiment_max_tokens
    if max_tokens is None:
        return {}
    return {"max_tokens": max_tokens}


def _log_voice_experiments(
    runtime: BridgeRuntime, voice_latency: VoiceLatencyContext | None
) -> None:
    if voice_latency is None:
        return
    log_voice_latency(
        logger,
        voice_latency,
        "agent_bridge_voice_experiments",
        short_reply=runtime.settings.voice_latency_experiment_short_reply,
        max_tokens=runtime.settings.voice_latency_experiment_max_tokens,
        disable_tools=runtime.settings.voice_latency_experiment_disable_tools,
    )

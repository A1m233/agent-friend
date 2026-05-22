"""错误模型转换层：内部异常 → 协议响应载体。

为什么单独抽一层：

- OpenAI 出口和 AG-UI 出口对**同一类内部错误**的协议形态不同，但**分类规则**
  应该一致——只在一处维护，两个出口分别取用，避免分歧
- ``Conversation`` / ``SessionStore`` / ``LLMClient`` 内部 raise 的异常信息往
  往含 IO / API key 细节，**不可**原样回给客户端；统一在这里清洗一道

映射表见 docs/requirements/006-agent-bridge/design.md §4.6.1。

未识别的异常一律 ``500 / internal_error`` + 拟人化 message——技术细节交给调
用方写日志，不暴露给客户端。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from agent import (
    PersonaAmbiguousError,
    PersonaNotFoundError,
    SessionCorruptError,
    SessionNotFoundError,
    SessionPersistError,
    random_fallback,
)
from llm_providers import (
    LLMAuthError,
    LLMBadRequestError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
)

_BRIDGE_FALLBACK_MESSAGES: tuple[str, ...] = (
    "我这边好像出了点小问题，能稍后再问我一遍吗？",
    "刚刚信号有点不稳，再来一次试试看？",
    "嗯⋯我突然走神了，可以再说一遍吗？",
    "这会儿脑子里有点乱，等会儿再聊？",
    "诶等等，我没接上你的话，可以再说一次吗？",
)
"""bridge 进程专属的拟人化兜底文案池。

design §4.6.4 明确要求：bridge 错误兜底**不污染** 001 的 ``FALLBACKS`` 池；
本常量与 :func:`agent.random_fallback` 混合使用，让用户在反复撞错时不会
看到完全相同的话术。
"""


@dataclass(frozen=True)
class ProtocolError:
    """协议层错误响应需要的全部信息。

    Attributes:
        http_status: OpenAI 出口的 HTTP 状态码。AG-UI 出口不直接用 —— AG-UI
            在 ``200 + RUN_ERROR`` 形态下表达错误，但用同一份 ``code`` / ``message``。
        code: 错误码标识（OpenAI envelope.error.code / AG-UI RUN_ERROR.code）。
        message: 用户可见文案。可恢复 / 内部错误用拟人化兜底；明确语义的不可
            恢复错误用诊断性短句（如"找不到指定的会话"）。
        recoverable: 是否属于"可恢复"类。本字段**不影响** wire 形态——OpenAI
            照样 4xx/5xx，AG-UI 照样 RUN_ERROR。仅供上层埋点 / 日志级别决策。
    """

    http_status: int
    code: str
    message: str
    recoverable: bool


def map_exception(exc: BaseException) -> ProtocolError:
    """把内部异常映射成 :class:`ProtocolError`。

    映射规则见 design §4.6.1。增改异常类型时同步更新映射表与 design 文档。
    """
    if isinstance(exc, LLMRateLimitError):
        return ProtocolError(
            http_status=429,
            code="rate_limit",
            message=_humane_message(),
            recoverable=True,
        )
    if isinstance(exc, (LLMNetworkError, LLMProviderError)):
        return ProtocolError(
            http_status=502,
            code="upstream_transient",
            message=_humane_message(),
            recoverable=True,
        )
    if isinstance(exc, LLMAuthError):
        return ProtocolError(
            http_status=401,
            code="auth_failed",
            message="后端身份校验失败，请联系管理员确认 API 凭证。",
            recoverable=False,
        )
    if isinstance(exc, LLMBadRequestError):
        return ProtocolError(
            http_status=400,
            code="bad_request",
            message="请求格式或参数不被后端模型接受。",
            recoverable=False,
        )
    if isinstance(exc, (PersonaNotFoundError, PersonaAmbiguousError)):
        return ProtocolError(
            http_status=404,
            code="persona_not_found",
            message="找不到指定的人设。",
            recoverable=False,
        )
    if isinstance(exc, SessionNotFoundError):
        return ProtocolError(
            http_status=404,
            code="session_not_found",
            message="找不到指定的会话。",
            recoverable=False,
        )
    if isinstance(exc, (SessionPersistError, SessionCorruptError)):
        return ProtocolError(
            http_status=500,
            code="session_io_error",
            message=_humane_message(),
            recoverable=False,
        )
    return ProtocolError(
        http_status=500,
        code="internal_error",
        message=_humane_message(),
        recoverable=False,
    )


def build_openai_error_envelope(err: ProtocolError) -> dict[str, Any]:
    """构造 OpenAI 错误响应 body / 流式 error chunk。

    同一份结构用在两种位置：

    - 非流式：作为 ``JSONResponse(content=...)`` 的 body
    - 流式：作为 SSE 中段的 error chunk（``data: <json>\\n\\n``）
    """
    return {
        "error": {
            "message": err.message,
            "type": "agent_friend_error",
            "code": err.code,
        }
    }


def _humane_message() -> str:
    """随机选一条拟人化兜底文案。

    bridge 专属池与 :func:`agent.random_fallback` 池 50/50 混选，避免用户撞到
    单一文案重复出现的诡异感（特别是在 voice 场景，TTS 出来的话听感是有"重复
    幻觉"风险的）。
    """
    if random.random() < 0.5:
        return random.choice(_BRIDGE_FALLBACK_MESSAGES)
    return random_fallback()


__all__ = [
    "ProtocolError",
    "build_openai_error_envelope",
    "map_exception",
]

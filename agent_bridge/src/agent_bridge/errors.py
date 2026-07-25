"""错误模型转换层：内部异常 → 协议响应载体。

为什么单独抽一层：

- OpenAI 出口和 AG-UI 出口对**同一类内部错误**的协议形态不同，但**分类规则**
  应该一致——只在一处维护，两个出口分别取用，避免分歧
- ``Conversation`` / ``SessionStore`` / ``LLMClient`` 内部 raise 的异常信息往
  往含 IO / API key 细节，**不可**原样回给客户端；统一在这里清洗一道

映射表见 docs/requirements/006-agent-bridge/design.md §4.6.1。

未识别的异常一律 ``500 / internal_error`` + 稳定用户文案——技术细节交给调用方
写日志，不暴露给客户端。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.runtime import SessionBusyError

from agent import (
    AttachmentError,
    ContextBaseOverBudgetError,
    ContextInputTooLargeError,
    PersonaAmbiguousError,
    PersonaNotFoundError,
    SessionCorruptError,
    SessionNotFoundError,
    SessionPersistError,
)
from llm_providers import (
    LLMAuthError,
    LLMBadRequestError,
    LLMContentPolicyError,
    LLMEmptyResponseError,
    LLMModelMetadataError,
    LLMNetworkError,
    LLMOutputBudgetExhaustedError,
    LLMProviderError,
    LLMRateLimitError,
)
from runtime_config import CredentialMissingError


class UnsupportedModelError(ValueError):
    """A requested model is absent from the public model catalog."""


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
        sanitize_failed_model_call: 后续模型上下文是否应把失败模型调用新增的
            事务替换为安全占位；原始事件仍保留供 UI、审计和诊断使用。
    """

    http_status: int
    code: str
    message: str
    recoverable: bool
    sanitize_failed_model_call: bool = False


def map_exception(exc: BaseException) -> ProtocolError:
    """把内部异常映射成 :class:`ProtocolError`。

    映射规则见 design §4.6.1。增改异常类型时同步更新映射表与 design 文档。
    """
    if isinstance(exc, AttachmentError):
        return ProtocolError(
            http_status=400,
            code=exc.code,
            message=str(exc),
            recoverable=False,
        )
    if isinstance(exc, UnsupportedModelError):
        return ProtocolError(
            http_status=400,
            code="unsupported_model",
            message="找不到这个模型，请刷新模型列表后再试。",
            recoverable=False,
        )
    if isinstance(exc, SessionBusyError):
        return ProtocolError(
            http_status=409,
            code="session_busy",
            message="当前会话正在处理消息，请等待这轮结束后再切换模型。",
            recoverable=True,
        )
    if isinstance(exc, CredentialMissingError):
        return ProtocolError(
            http_status=503,
            code="credential_missing",
            message="模型凭据尚未配置，请先在设置中完成配置。",
            recoverable=True,
        )
    if isinstance(exc, ContextBaseOverBudgetError):
        return ProtocolError(
            http_status=400,
            code=exc.code,
            message="当前模型的基础提示词与工具已超过上下文预算，请切换模型或精简配置。",
            recoverable=False,
        )
    if isinstance(exc, ContextInputTooLargeError):
        return ProtocolError(
            http_status=400,
            code=exc.code,
            message="当前输入超过模型上下文预算，请缩短内容或减少附件后重试。",
            recoverable=False,
        )
    if isinstance(exc, LLMModelMetadataError):
        return ProtocolError(
            http_status=500,
            code="model_context_profile_invalid",
            message="模型运行限制配置不完整，请更新应用或模型配置后重试。",
            recoverable=False,
        )
    if isinstance(exc, LLMOutputBudgetExhaustedError):
        return ProtocolError(
            http_status=502,
            code="output_budget_exhausted",
            message="模型把本轮输出额度用于推理，未生成可见回答。请重试或提高输出额度。",
            recoverable=True,
        )
    if isinstance(exc, LLMEmptyResponseError):
        return ProtocolError(
            http_status=502,
            code="empty_model_response",
            message="模型没有返回可见回答，这轮未完成。请稍后重试。",
            recoverable=True,
        )
    if isinstance(exc, LLMRateLimitError):
        return ProtocolError(
            http_status=429,
            code="rate_limit",
            message="模型服务这会儿过载了，这轮没有生成完整回答。稍后可以重试。",
            recoverable=True,
        )
    if isinstance(exc, (LLMNetworkError, LLMProviderError)):
        return ProtocolError(
            http_status=502,
            code="upstream_transient",
            message="模型服务连接不稳定，这轮没有生成完整回答。稍后可以重试。",
            recoverable=True,
        )
    if isinstance(exc, LLMAuthError):
        return ProtocolError(
            http_status=401,
            code="auth_failed",
            message="后端身份校验失败，请联系管理员确认 API 凭证。",
            recoverable=False,
        )
    if isinstance(exc, LLMContentPolicyError):
        return ProtocolError(
            http_status=400,
            code="content_policy",
            message=(
                "请求内容触发了后端模型的安全限制，这轮没有生成完整回答。"
                "可以换个问法或切换模型后重试。"
            ),
            recoverable=False,
            sanitize_failed_model_call=True,
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
            message="会话保存时出了问题，这轮状态可能没有完整记录。请稍后重试。",
            recoverable=False,
        )
    return ProtocolError(
        http_status=500,
        code="internal_error",
        message="这轮回答中断了，没有生成完整回复。请稍后重试。",
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


__all__ = [
    "ProtocolError",
    "UnsupportedModelError",
    "build_openai_error_envelope",
    "map_exception",
]

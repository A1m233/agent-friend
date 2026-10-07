"""错误模型转换层：内部异常 → 协议响应载体。

为什么单独抽一层：

- OpenAI 出口和 AG-UI 出口对**同一类内部错误**的协议形态不同，但**分类规则**
  应该一致——只在一处维护，两个出口分别取用，避免分歧
- ``Conversation`` / ``SessionStore`` / ``LanguageModel`` 内部 raise 的异常信息往
  往含 IO / API key 细节，**不可**原样回给客户端；统一在这里清洗一道

映射表见 docs/requirements/006-agent-bridge/design.md §4.6.1。

未识别的异常一律 ``500 / internal_error`` + 稳定用户文案——技术细节交给调用方
写日志，不暴露给客户端。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.runtime import SessionBusyError
from agent.session_requests.errors import SessionRequestError

from agent import (
    AttachmentError,
    ContextBaseOverBudgetError,
    ContextCompactionUnavailableError,
    ContextInputTooLargeError,
    MalformedToolCallError,
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
    LLMContextWindowError,
    LLMEmptyResponseError,
    LLMError,
    LLMModelMetadataError,
    LLMModelUnavailableError,
    LLMNetworkError,
    LLMOutputBudgetExhaustedError,
    LLMPaymentRequiredError,
    LLMPermissionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMStreamInterruptedAfterOutputError,
    LLMTransientProviderError,
    LLMUnsupportedParameterError,
)
from runtime_config import CredentialMissingError


class UnsupportedModelError(ValueError):
    """A requested model is absent from the public model catalog."""


class UnsupportedReasoningPresetError(ValueError):
    """A reasoning preset is absent from the selected model definition."""


class InvalidRuntimeConfigError(ValueError):
    """A runtime-config mutation has no valid fields."""


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
    reason: str | None = None
    recovery_action: str | None = None

    def recovery_details(self) -> dict[str, str] | None:
        if self.reason is None and self.recovery_action is None:
            return None
        return {
            **({"reason": self.reason} if self.reason else {}),
            **({"recoveryAction": self.recovery_action} if self.recovery_action else {}),
        }


def map_exception(exc: BaseException) -> ProtocolError:
    """把内部异常映射成 :class:`ProtocolError`。

    映射规则见 design §4.6.1。增改异常类型时同步更新映射表与 design 文档。
    """
    if isinstance(exc, SessionRequestError):
        return ProtocolError(http_status=409, code=exc.kind, message=exc.message, recoverable=False)
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
    if isinstance(exc, UnsupportedReasoningPresetError):
        return ProtocolError(
            http_status=400,
            code="unsupported_reasoning_preset",
            message="这个模型不支持所选推理强度，请刷新模型列表后再试。",
            recoverable=False,
        )
    if isinstance(exc, InvalidRuntimeConfigError):
        return ProtocolError(
            http_status=400,
            code="invalid_runtime_config",
            message="请至少选择模型或推理强度中的一项。",
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
    if isinstance(exc, ContextCompactionUnavailableError):
        if isinstance(exc.__cause__, LLMError):
            cause = map_exception(exc.__cause__)
            return ProtocolError(
                http_status=cause.http_status,
                code=exc.code,
                message="会话压缩未完成，已保留原历史。" + cause.message,
                recoverable=cause.recoverable,
                reason=exc.reason,
                recovery_action="compact_then_resume"
                if cause.recoverable
                else "adjust_configuration",
            )
        return ProtocolError(
            http_status=503,
            code=exc.code,
            message=(
                "当前模型不支持会话压缩参数，已保留原历史。请切换模型或修正配置后继续。"
                if exc.reason == "unsupported_model_parameter"
                else "压缩后需要保留的输入仍超过预算，已保留原历史。请调整输入或模型容量后继续。"
                if exc.reason == "retained_context_over_budget"
                else "部分上下文缺少可靠的用量证据，已保留原历史。可以尝试压缩后继续。"
                if exc.reason == "input_cost_unknown"
                else "仍有工具执行结果待确认，已保留原历史。请先等待或核实执行状态。"
                if exc.reason == "tool_transaction_pending"
                else "当前模型连接无法安全重建上下文，已保留原历史。请调整模型连接后继续。"
                if exc.reason == "checkpoint_unsupported"
                else "会话已变化，已保留最新输入。请刷新后继续最新任务。"
                if exc.reason == "source_changed"
                else "上下文压缩没有进一步缩小输入，已保留原历史。请调整任务范围或模型容量后继续。"
                if exc.reason == "compaction_no_progress"
                else "本次压缩已达到处理上限，已保留原历史。请调整任务范围或模型容量后继续。"
                if exc.reason
                in {"summary_segment_limit", "summary_attempt_limit", "summary_input_too_large"}
                else "无法确认当前任务的原始目标，已保留原历史。请在原会话补充当前目标。"
                if exc.reason == "task_source_unavailable"
                else "会话压缩暂时失败，已保留原历史。可以尝试压缩后继续；若仍失败，请调整输入或模型配置。"
            ),
            recoverable=True,
            reason=exc.reason,
            recovery_action=(
                "adjust_configuration"
                if exc.reason in {"unsupported_model_parameter", "checkpoint_unsupported"}
                else "adjust_input"
                if exc.reason
                in {
                    "retained_context_over_budget",
                    "compaction_no_progress",
                    "summary_segment_limit",
                    "summary_attempt_limit",
                    "summary_input_too_large",
                    "task_source_unavailable",
                }
                else "wait"
                if exc.reason == "tool_transaction_pending"
                else "retry"
                if exc.reason == "source_changed"
                else "compact_then_resume"
            ),
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
    if isinstance(exc, MalformedToolCallError):
        return ProtocolError(
            http_status=502,
            code="malformed_tool_call",
            message=(
                "模型连续生成了无效的工具参数；工具没有执行，也没有发起授权。"
                "请重试，仍失败时可以切换模型。"
            ),
            recoverable=True,
            sanitize_failed_model_call=True,
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
    if isinstance(exc, LLMTransientProviderError) and exc.provider_error_type in {
        "engine_overloaded_error",
        "server_is_overloaded",
        "provider_overloaded",
    }:
        return ProtocolError(
            http_status=503,
            code="upstream_transient",
            message="模型服务暂时过载，这次没能生成回答。稍后再试一下。",
            recoverable=True,
        )
    if isinstance(exc, LLMRateLimitError):
        return ProtocolError(
            http_status=429,
            code="rate_limit",
            message="模型服务暂时限制了请求频率，这次没能生成回答。请稍后再试。",
            recoverable=True,
        )
    if isinstance(exc, LLMPaymentRequiredError):
        return ProtocolError(
            http_status=402,
            code="payment_required",
            message="模型账户或当前 Key 额度不足，请检查额度或更换凭据后重试。",
            recoverable=False,
        )
    if isinstance(exc, LLMPermissionError):
        return ProtocolError(
            http_status=403,
            code="permission_denied",
            message="当前 Key 或路由策略不允许这次模型调用，请检查权限或更换模型。",
            recoverable=False,
        )
    if isinstance(exc, LLMModelUnavailableError):
        return ProtocolError(
            http_status=503,
            code="model_route_unavailable",
            message=(
                "所选模型已不可用，请重新选择模型。"
                if exc.availability_code == "model_not_configured"
                else "当前模型没有满足必要能力与隐私约束的可用路由，请稍后重试或切换模型。"
            ),
            recoverable=exc.retryable,
        )
    if isinstance(exc, LLMStreamInterruptedAfterOutputError):
        return ProtocolError(
            http_status=502,
            code="stream_interrupted_after_output",
            message="模型连接在已经返回部分内容后中断；已返回内容不会被自动重放。",
            recoverable=True,
        )
    if isinstance(exc, (LLMNetworkError, LLMTransientProviderError)):
        return ProtocolError(
            http_status=502,
            code="upstream_transient",
            message="模型服务连接不稳定，这轮没有生成完整回答。稍后可以重试。",
            recoverable=True,
        )
    if isinstance(exc, LLMProviderError):
        if exc.error_evidence is not None and exc.error_evidence.source == "protocol":
            if exc.error_evidence.code == "unsupported_output_capability":
                return ProtocolError(
                    502,
                    "model_output_unsupported",
                    "模型只返回了当前连接尚未接入的输出类型，没有可用回答；已有步骤已保留，请检查连接能力或切换模型。",
                    False,
                )
            return ProtocolError(
                502,
                "model_protocol_error",
                "模型响应的内容或工具调用不完整、存在冲突；本次响应中的工具未执行，已有步骤已保留。请检查连接兼容性后继续。",
                False,
            )
        return ProtocolError(
            http_status=502,
            code="model_provider_error",
            message="模型调用失败，原因尚未确定。请查看诊断信息或检查模型连接配置后重试。",
            recoverable=False,
        )
    if isinstance(exc, LLMAuthError):
        return ProtocolError(
            http_status=401,
            code="auth_failed",
            message="模型凭据无效或已失效，请在“模型与凭据”设置中重新配置。",
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
    if isinstance(exc, LLMUnsupportedParameterError):
        return ProtocolError(
            400,
            "unsupported_model_parameter",
            "当前模型参数未通过本地校验，请调整模型配置后重试。",
            False,
        )
    if isinstance(exc, LLMContextWindowError):
        return ProtocolError(
            400,
            "context_window_exceeded",
            "模型报告上下文超限，请缩短输入或切换更大上下文的模型。",
            False,
        )
    if isinstance(exc, LLMBadRequestError):
        if exc.error_evidence is not None and exc.error_evidence.source == "local_validation":
            return ProtocolError(
                400,
                "model_request_invalid",
                "模型请求未通过本地校验，本次没有发送请求。请检查模型配置与请求参数。",
                False,
            )
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
            **({"details": err.recovery_details()} if err.recovery_details() else {}),
        }
    }


__all__ = [
    "InvalidRuntimeConfigError",
    "ProtocolError",
    "UnsupportedModelError",
    "UnsupportedReasoningPresetError",
    "build_openai_error_envelope",
    "map_exception",
]

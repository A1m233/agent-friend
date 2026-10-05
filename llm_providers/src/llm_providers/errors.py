"""LLM Provider 抽象层错误模型。

把底层（LiteLLM / OpenAI SDK）的各种异常抹平成项目自定义的错误类型，
``LLMError`` 子类，让上层（Conversation / CLI）只需依赖本模块的异常类型，
不需要 import LiteLLM。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.1.3。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .stream_events import LLMResponseDiagnostics, LLMUsage


@dataclass(frozen=True)
class ProtocolEvidence:
    """Bounded structural metadata; never an event or provider payload."""

    stage: str
    event_number: int
    event_type: str | None
    event_type_state: str
    event_type_digest: str | None
    fields: tuple[tuple[str, str], ...]
    unknown_field_count: int
    item_count: int
    terminal_seen: bool
    output_types: tuple[str, ...] = ()
    output_types_truncated: bool = False
    schema_revision: int = 1
    recent_event_types: tuple[str, ...] = ()
    expected_item_type: str | None = None
    actual_item_type: str | None = None
    part_type: str | None = None
    output_index: int | None = None
    content_index: int | None = None
    item_index_matches: bool | None = None


@dataclass(frozen=True)
class ErrorEvidence:
    """Content-free failure evidence, safe for application logs and diagnostics."""

    source: str
    category: str
    code: str | None = None
    error_type: str | None = None
    status_code: int | None = None
    request_ref: str | None = None
    response_ref: str | None = None
    message_digest: str | None = None
    message_length: int = 0
    retry_after_seconds: float | None = None
    protocol: ProtocolEvidence | None = None
    timeout_stage: str | None = None
    timeout_seconds: float | None = None
    timeout_elapsed_seconds: float | None = None
    timeout_source: str | None = None
    timeout_last_activity: str | None = None
    timeout_stage_elapsed_seconds: float | None = None


class LLMError(Exception):
    """所有 LLM 相关错误的基类。"""

    error_evidence: ErrorEvidence | None = None


class LLMAuthError(LLMError):
    """API key 错误或缺失。CLI 层期望行为：fail fast。"""


class LLMRateLimitError(LLMError):
    """被 Provider 限速。上层期望行为：记录并使用兜底话术。"""


class LLMNetworkError(LLMError):
    """网络错误或超时。LLMClient 有限重试用尽后交给上层兜底。"""


class LLMStreamTimeoutError(LLMNetworkError):
    """A provider stream stopped making observable progress for too long."""


class LLMStreamInterruptedAfterOutputError(LLMNetworkError):
    """A stream failed after visible output crossed the replay-safe boundary."""

    reason_code = "stream_interrupted_after_output"


class LLMBadRequestError(LLMError):
    """请求格式错误。多半是 bug，CLI 层不重试。"""


class LLMImageInputUnsupportedError(LLMBadRequestError):
    """Explicit provider rejection of image input, without retaining provider text."""


class LLMUnsupportedParameterError(LLMBadRequestError):
    """The local SDK rejected a model parameter before sending a request."""

    reason_code = "unsupported_model_parameter"
    error_source = "sdk_validation"


class LLMContextWindowError(LLMBadRequestError):
    """请求输入超过当前 Provider / Model 的上下文窗口。"""


class LLMModelMetadataError(LLMError):
    """无法可靠解析当前模型的运行限制，禁止带着猜测值继续请求。"""


class LLMContentPolicyError(LLMError):
    """请求内容被 Provider 的安全策略拒绝；原样重试通常仍会失败。"""


class LLMPaymentRequiredError(LLMError):
    """Credential is valid, but the account or key has insufficient quota."""


class LLMPermissionError(LLMError):
    """Credential is valid, but policy, region, or permission denied the call."""


class LLMProviderError(LLMError):
    """其他 Provider 侧错误。CLI 层期望行为：兜底话术。"""


class LLMTransientProviderError(LLMProviderError):
    """Provider 临时不可用，可在未产出结果时有限重试。

    ``engine_overloaded_error`` 等 Provider 专有错误需要把退避提示和请求标识
    带到统一重试层；这些字段只承载无内容诊断信息，不保存响应正文。
    """

    def __init__(
        self,
        message: str,
        *,
        provider_error_type: str | None = None,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_error_type = provider_error_type
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.provider_request_id = provider_request_id


class LLMModelUnavailableError(LLMProviderError):
    """The selected model has no endpoint satisfying the required route policy."""

    def __init__(
        self,
        message: str,
        *,
        availability_code: str | None = None,
        retryable: bool = False,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.availability_code = availability_code
        self.retryable = retryable
        self.status_code = status_code
        self.provider_request_id = provider_request_id


class LLMEmptyResponseError(LLMProviderError):
    """Provider 正常结束了请求，但没有返回正文或工具调用。"""

    reason_code = "empty_model_response"


class LLMIncompleteResponseError(LLMProviderError):
    """Non-success terminal response; partial tool calls must not be executed."""

    reason_code = "incomplete_model_response"

    def __init__(
        self,
        stop_reason: str,
        *,
        usage: LLMUsage | None = None,
        diagnostics: LLMResponseDiagnostics | None = None,
    ) -> None:
        super().__init__(f"模型响应未完整完成：{stop_reason}")
        self.stop_reason = stop_reason
        self.usage = usage
        self.diagnostics = diagnostics


class LLMOutputBudgetExhaustedError(LLMEmptyResponseError):
    """Provider 把输出预算耗尽在推理内容中，未产生可见正文。"""

    reason_code = "output_budget_exhausted"

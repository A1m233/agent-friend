"""``WebSearchTool``：对 LLM 暴露的"互联网搜索"能力。

本模块定义 web_search 子领域的对外接口集：

- :class:`SearchHit` — 单条搜索结果的数据结构（provider 解耦）
- :class:`WebSearchProvider` — 搜索 provider 的最小契约（仅 web_search 内部消费）
- :class:`WebSearchTool` — 实现 :class:`agent.tools.Tool` Protocol 的工具类
- :class:`WebSearchError` 体系 — provider 实现统一向外抛的错误类型

``WebSearchTool`` 不依赖任何具体 provider，通过构造期注入 :class:`WebSearchProvider`
解耦。具体 provider 实现见 :mod:`.providers` 子包。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.7。
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable

from ...errors import ToolError
from ...protocol import ToolResult

# ===== 异常体系：provider 实现统一向外抛这些 =====


class WebSearchError(ToolError):
    """所有 :mod:`web_search` 模块异常的基类。继承自 :class:`ToolError` 体系。"""

    error_kind: ClassVar[str] = "provider"
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        request_id: str | None = None,
        exception_type: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.request_id = request_id
        self.exception_type = exception_type
        self.is_retryable = self.retryable if retryable is None else retryable

    def diagnostic_meta(self) -> dict[str, Any]:
        """Return safe structured diagnostics without exposing provider messages."""

        meta: dict[str, Any] = {
            "error_kind": self.error_kind,
            "exception_type": self.exception_type or type(self).__name__,
            "retryable": self.is_retryable,
        }
        if self.status_code is not None:
            meta["status_code"] = self.status_code
        if self.retry_after_seconds is not None:
            meta["retry_after_seconds"] = self.retry_after_seconds
        if self.request_id:
            meta["request_id"] = self.request_id
        return meta


class WebSearchAuthError(WebSearchError):
    """API key 错或失效。"""

    error_kind = "auth"


class WebSearchRateLimitError(WebSearchError):
    """触达 provider 的限流 / 配额 / 临时禁用。"""

    error_kind = "rate_limit"
    retryable = True


class WebSearchNetworkError(WebSearchError):
    """网络错或超时。"""

    error_kind = "network"
    retryable = True


class WebSearchProviderError(WebSearchError):
    """其它 provider 侧错（含未分类）。"""

    error_kind = "provider"


# ===== 数据结构 =====


@dataclass(frozen=True)
class SearchHit:
    """单条搜索结果的 provider 解耦表达。

    Attributes:
        title: 结果标题。
        url: 结果 URL。
        snippet: 摘要 / 正文片段。

    Note:
        所有 :class:`WebSearchProvider` 实现都返回 ``list[SearchHit]``——
        把 Tavily / Bocha / 智谱等各自的原生响应规范化到同一形态，
        :class:`WebSearchTool` 内部的 :func:`_format` 才能 provider-agnostic。
    """

    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class SearchResponse:
    """Provider-neutral successful response plus safe diagnostic metadata."""

    hits: list[SearchHit]
    request_id: str | None = None
    provider_duration_seconds: float | None = None


@dataclass(frozen=True)
class WebSearchRetryPolicy:
    """Bound retries by both attempt count and the interactive tool deadline."""

    max_attempts: int = 3
    deadline_seconds: float = 28.0
    attempt_timeout_seconds: float = 12.0
    minimum_attempt_seconds: float = 1.0
    base_delay_seconds: float = 0.5
    max_retry_after_seconds: float = 5.0


# ===== Provider 协议 =====


@runtime_checkable
class WebSearchProvider(Protocol):
    """搜索 provider 的最小契约。

    Note:
        本 Protocol **仅 web_search 子包内部消费**，不暴露到 ``agent.tools``
        公共 API（详见 005 design §6.1）——引擎层只看到 :class:`Tool` /
        :class:`ToolRegistry`，``WebSearchProvider`` 是 web_search 模块的
        实现细节。

        所有 provider 实现负责把各自 SDK 的异常 catch 后映射成本模块自有
        :class:`WebSearchError` 子类向上抛。
    """

    name: ClassVar[str]
    """provider 标识，写入 :class:`ToolResult` ``meta["provider"]`` 便于观测。"""

    def search(
        self,
        query: str,
        max_results: int,
        *,
        timeout_seconds: float,
    ) -> SearchResponse:
        """同步发起搜索请求。

        Args:
            query: 搜索查询字符串。
            max_results: 期望返回的最大结果数；provider 实际返回数可少于此值。

        Returns:
            Provider-neutral response。无结果时 ``hits`` 为空列表。

        Raises:
            WebSearchAuthError: API key 错或失效
            WebSearchRateLimitError: 触达限流 / 配额
            WebSearchNetworkError: 网络错或超时
            WebSearchProviderError: 其它 provider 侧错
        """
        ...


# ===== Tool 主体 =====


class WebSearchTool:
    """对 LLM 暴露的"互联网搜索"工具，实现 :class:`agent.tools.Tool` Protocol。

    LLM 永远只看到 ``name="web_search"``——切 / 加 provider 不改这三件套，
    LLM 的工具选择行为不受 provider 替换影响。

    Args:
        provider: :class:`WebSearchProvider` 实例。由 :func:`agent.tools.make_default_registry`
            根据当前配置（env var）挑出对应 provider 注入。
        max_results: 期望返回的最大结果数，默认 5。
    """

    name: ClassVar[str] = "web_search"
    execution_kind: ClassVar[str] = "parallel_read"
    timeout_seconds: ClassVar[float] = 30.0
    description: ClassVar[str] = (
        "搜索互联网获取实时或可能变化的信息。\n\n"
        "**何时使用**：\n"
        "- 用户问及任何时效性信息：新闻、天气、汇率、股价、赛事结果\n"
        "- 用户问及『今天』『现在』『最新』『最近』『目前』等时效词相关的内容\n"
        "- 涉及具体日期 / 时间点之后发生的事\n"
        "- 涉及具体公司、产品、人物、作品、视频、梗、活动、政策或数字细节，且你不能 100% 确定\n\n"
        "- 即使没有『最新 / 现在』等时效词，只要答案依赖具体产品或平台的当前能力、配置、版本、价格、负责人、规则、精确数字或事件状态\n"
        "- 具体产品的当前操作步骤、设置菜单路径、账号 / 支付政策、客服联系方式或故障机制\n"
        "- 在多轮排障中，用户补充了新现象，而你需要依赖具体平台规则才能给出诊断或下一步\n"
        "- 你发现自己正准备用『印象里 / 应该 / 大概 / 一般是』补一个具体外部事实\n\n"
        "**何时不要使用**：\n"
        "- 用户问的是你和用户过去是否聊过、推荐过或约定过；这类问题用历史对话工具核对\n"
        "- 用户要的是创作、改写、代码解释、情绪陪伴或明确不需要外部资料的判断\n"
        "- 用户问的是稳定定义、通用概念、纯推理或主观评价，不依赖某个具体对象的当前外部事实\n"
        "- 当前对话里已经有用户提供的事实，且用户只是让你基于这些信息整理或推理\n\n"
        "**参数来源与证据边界**：\n"
        "- query 用清晰自然语言描述要找的信息，关键词 2~5 个，不超过一句话\n"
        "- query 必须包含正确的当前年份（参见 system prompt 中『当前时间』），不要默认用训练数据时期的年份\n"
        "- 搜索结果只证明外部资料里出现过这些内容，不能证明你过去和用户聊过\n"
        "- 只使用结果里实际出现的字段；标题、片段或来源没有给出的细节不要补\n\n"
        "**失败与重试**：\n"
        "- 结果不相关或没命中时，可换 query 重试 1~2 次\n"
        "- 仍没命中就说没直接找到，向用户要平台、时间、画面或其他线索\n"
        "- 不要对用户说 provider、API、SDK、鉴权、限流、stack trace 等技术字眼"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "搜索查询。用清晰的自然语言描述要找的信息，"
                    "**必须包含正确的当前年份**（参见 system prompt 中『当前时间』）。"
                ),
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        provider: WebSearchProvider,
        max_results: int = 5,
        *,
        retry_policy: WebSearchRetryPolicy | None = None,
        _clock: Callable[[], float] = time.monotonic,
        _sleep: Callable[[float], None] = time.sleep,
        _jitter: Callable[[], float] = random.random,
    ) -> None:
        self._provider = provider
        self._max_results = max_results
        self._retry_policy = retry_policy or WebSearchRetryPolicy()
        self._clock = _clock
        self._sleep = _sleep
        self._jitter = _jitter

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        query = args["query"]
        started_at = self._clock()
        deadline = started_at + self._retry_policy.deadline_seconds
        attempts: list[dict[str, Any]] = []

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining < self._retry_policy.minimum_attempt_seconds:
                break
            timeout = min(self._retry_policy.attempt_timeout_seconds, remaining)
            attempt_started_at = self._clock()
            try:
                response = self._provider.search(
                    query,
                    self._max_results,
                    timeout_seconds=timeout,
                )
            except WebSearchError as exc:
                attempt_meta = {
                    "attempt": attempt,
                    "duration_seconds": self._clock() - attempt_started_at,
                    "outcome": "error",
                    **exc.diagnostic_meta(),
                }
                attempts.append(attempt_meta)
                delay = self._retry_delay(exc, attempt)
                can_retry = (
                    exc.is_retryable
                    and attempt < self._retry_policy.max_attempts
                    and delay is not None
                    and deadline - self._clock()
                    >= delay + self._retry_policy.minimum_attempt_seconds
                )
                if not can_retry:
                    return self._error_result(exc, started_at, attempts)
                assert delay is not None
                attempt_meta["retry_delay_seconds"] = delay
                self._sleep(delay)
                continue

            hits = response.hits
            attempts.append(
                {
                    "attempt": attempt,
                    "duration_seconds": self._clock() - attempt_started_at,
                    "outcome": "success",
                }
            )
            meta: dict[str, Any] = {
                "duration_seconds": self._clock() - started_at,
                "result_count": len(hits),
                "provider": self._provider.name,
                "attempt_count": attempt,
                "retries_taken": attempt - 1,
                "attempts": attempts,
                "source_urls": [hit.url for hit in hits],
            }
            if response.request_id:
                meta["request_id"] = response.request_id
            if response.provider_duration_seconds is not None:
                meta["provider_duration_seconds"] = response.provider_duration_seconds
            return ToolResult(text=_format(query, hits), is_error=False, meta=meta)

        deadline_error = WebSearchNetworkError(
            "web search deadline exhausted",
            exception_type="WebSearchDeadlineError",
        )
        return self._error_result(deadline_error, started_at, attempts)

    def _retry_delay(self, exc: WebSearchError, attempt: int) -> float | None:
        retry_after = exc.retry_after_seconds
        if retry_after is not None:
            if retry_after < 0 or retry_after > self._retry_policy.max_retry_after_seconds:
                return None
            return retry_after
        ceiling = self._retry_policy.base_delay_seconds * (2 ** (attempt - 1))
        jitter = float(self._jitter())
        bounded_jitter: float = min(1.0, max(0.0, jitter))
        return float(ceiling * bounded_jitter)

    def _error_result(
        self,
        exc: WebSearchError,
        started_at: float,
        attempts: list[dict[str, Any]],
    ) -> ToolResult:
        if isinstance(exc, WebSearchAuthError):
            text = "搜索失败：API 鉴权错误"
        elif isinstance(exc, WebSearchRateLimitError):
            text = "搜索失败：触达限流"
        elif isinstance(exc, WebSearchNetworkError):
            text = "搜索失败：网络错误"
        elif exc.is_retryable:
            text = "搜索失败：搜索服务暂时不可用"
        else:
            text = f"搜索失败：{exc}"
        meta = {
            "duration_seconds": self._clock() - started_at,
            "provider": self._provider.name,
            "attempt_count": len(attempts),
            "retries_taken": max(0, len(attempts) - 1),
            "attempts": attempts,
            "final_outcome": "failed",
            **exc.diagnostic_meta(),
        }
        return ToolResult(text=text, is_error=True, meta=meta)


def _format(query: str, hits: list[SearchHit]) -> str:
    """把 :class:`SearchHit` 列表编码为有明确来源的不可信 JSON 数据。

    Provider 无关——任何 provider 返回的 hits 都经过同一格式化逻辑，
    便于未来加新 provider 时复用 + 视觉一致。

    回答规则属于 system prompt / tool description，不混入无指令权的结果正文。
    JSON 编码也避免第三方 title / snippet 闭合自定义标签伪装外围说明。
    """
    payload = {
        "source_type": "web_search",
        "trust": "untrusted_data",
        "query": query,
        "status": "ok" if hits else "no_results",
        "results": [
            {"title": hit.title, "url": hit.url, "snippet": hit.snippet[:500]} for hit in hits
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

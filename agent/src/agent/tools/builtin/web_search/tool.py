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
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from ....tool_runtime.evidence import (
    PublicEvidence,
    evidence_result_meta,
    make_public_evidence,
)
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
    score: float | None = None
    published_at: str | None = None


WebSearchTimeRange = Literal["day", "week", "month", "year"]


@dataclass(frozen=True)
class WebSearchRequest:
    """Provider-neutral request for one bounded search query."""

    query: str
    max_results: int
    include_domains: tuple[str, ...] = ()
    time_range: WebSearchTimeRange | None = None


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
        request: WebSearchRequest,
        *,
        timeout_seconds: float,
    ) -> SearchResponse:
        """同步发起搜索请求。

        Args:
            request: provider-neutral 查询、结果上限和结构化过滤。

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
        "- 只有用户明确要求某个时间范围，或问题确实依赖最新状态时才加入年份/日期；寻找既有帖子时不要自行附加当前年份\n"
        "- 用户明确指定站点时优先使用 include_domains，而不是只把平台名拼进 query\n"
        "- 只有不同标题片段、作者、主题或时间线索能提供互补召回时，才给 alternate_queries；最多 2 条\n"
        "- 搜索结果只证明外部资料里出现过这些内容，不能证明你过去和用户聊过\n"
        "- 只使用结果里实际出现的字段；标题、片段或来源没有给出的细节不要补\n\n"
        "**失败与重试**：\n"
        "- 参数错误按返回的字段提示修正；网络/服务错误不等于未命中，工具已执行有界重试，"
        "换关键词不能修复连接故障。持续失败时改用其它可用取证路径，或说明当前能力受限；"
        "只有连接状态变化或稍后恢复检查时才值得再次调用同一路线\n"
        "- 当前结果窗口未命中不等于目标不存在；出现新的作者、别名、标题、站点、时间或链接线索时，可以据此改写查询或打开来源继续取证\n"
        "- 只有证据路径已经耗尽，或确实缺少用户独有信息时，才说明当前未定位并向用户询问必要线索\n"
        "- 不要对用户说 provider、API、SDK、鉴权、限流、stack trace 等技术字眼"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "主要搜索查询。用清晰自然语言描述要找的信息；只有真实时间意图存在时"
                    "才加入年份或日期，寻找既有内容时不要自行附加当前年份。"
                ),
            },
            "alternate_queries": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 2,
                "description": "可选的互补查询，最多 2 条；不要只做同义重复。",
            },
            "include_domains": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
                "description": "可选站点 hostname，例如 xiaohongshu.com；不要传 URL 或路径。",
            },
            "time_range": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
                "description": "可选时间范围；没有真实时间约束时省略。",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
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
        try:
            queries, include_domains, time_range = _parse_args(args)
        except ValueError as exc:
            return ToolResult(
                text=f"搜索失败：参数无效：{exc}。请修正对应字段后重试。",
                is_error=True,
                meta={
                    "provider": self._provider.name,
                    "error_kind": "input",
                    "exception_type": type(exc).__name__,
                    "retryable": False,
                },
            )

        started_at = self._clock()
        deadline = started_at + self._retry_policy.deadline_seconds
        attempts: list[dict[str, Any]] = []
        hits: list[SearchHit] = []
        seen_urls: set[str] = set()
        request_ids: list[str] = []
        provider_duration_seconds = 0.0
        executed_queries = 0
        last_error: WebSearchError | None = None

        for query_index, query in enumerate(queries):
            if deadline - self._clock() < self._retry_policy.minimum_attempt_seconds:
                break
            executed_queries += 1
            response, error = self._search_one(
                WebSearchRequest(
                    query=query,
                    max_results=self._max_results,
                    include_domains=include_domains,
                    time_range=time_range,
                ),
                query_index=query_index,
                deadline=deadline,
                attempts=attempts,
            )
            if response is None:
                last_error = error
                if isinstance(error, (WebSearchAuthError, WebSearchRateLimitError)):
                    break
                continue
            if response.request_id:
                request_ids.append(response.request_id)
            if response.provider_duration_seconds is not None:
                provider_duration_seconds += response.provider_duration_seconds
            for hit in response.hits:
                dedupe_key = _normalized_result_url(hit.url)
                if dedupe_key in seen_urls:
                    continue
                seen_urls.add(dedupe_key)
                hits.append(hit)
                if len(hits) >= 10:
                    break
            if len(hits) >= 10:
                break

        if not hits and last_error is not None:
            return self._error_result(last_error, started_at, attempts)

        evidence = [
            make_public_evidence(
                hit.url,
                source="web_search_result",
                label=hit.title,
            )
            for hit in hits
        ]
        meta: dict[str, Any] = {
            "duration_seconds": self._clock() - started_at,
            "result_count": len(hits),
            "provider": self._provider.name,
            "attempt_count": len(attempts),
            "retries_taken": max(0, len(attempts) - executed_queries),
            "attempts": attempts,
            "query_count": executed_queries,
            "requested_query_count": len(queries),
            "source_urls": [hit.url for hit in hits],
            "evidence_keys": [source.normalized_url for source in evidence],
            "include_domains": list(include_domains),
            "time_range": time_range,
            **evidence_result_meta(evidence),
        }
        if request_ids:
            meta["request_id"] = request_ids[0]
            meta["request_ids"] = request_ids
        if provider_duration_seconds:
            meta["provider_duration_seconds"] = provider_duration_seconds
        return ToolResult(
            text=_format(queries[0], hits, queries=queries, evidence=evidence),
            is_error=False,
            meta=meta,
        )

    def _search_one(
        self,
        request: WebSearchRequest,
        *,
        query_index: int,
        deadline: float,
        attempts: list[dict[str, Any]],
    ) -> tuple[SearchResponse | None, WebSearchError | None]:
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining < self._retry_policy.minimum_attempt_seconds:
                break
            timeout = min(self._retry_policy.attempt_timeout_seconds, remaining)
            attempt_started_at = self._clock()
            try:
                response = self._provider.search(request, timeout_seconds=timeout)
            except WebSearchError as exc:
                attempt_meta = {
                    "query_index": query_index,
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
                    return None, exc
                assert delay is not None
                attempt_meta["retry_delay_seconds"] = delay
                self._sleep(delay)
                continue

            attempts.append(
                {
                    "query_index": query_index,
                    "attempt": attempt,
                    "duration_seconds": self._clock() - attempt_started_at,
                    "outcome": "success",
                }
            )
            return response, None

        return (
            None,
            WebSearchNetworkError(
                "web search deadline exhausted",
                exception_type="WebSearchDeadlineError",
            ),
        )

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
            text = (
                f"搜索失败：网络错误；本次已尝试 {len(attempts)} 次，有界重试已结束，"
                "未取得搜索结果。这不表示没有匹配内容；换关键词不能修复连接故障。"
                "请改用其它可用搜索或网页读取路径；连接状态变化或稍后检查恢复时再重试。"
            )
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
        if isinstance(exc, (WebSearchNetworkError, WebSearchAuthError, WebSearchRateLimitError)):
            meta["no_progress_family"] = f"search_service:{self._provider.name}:{exc.error_kind}"
        return ToolResult(text=text, is_error=True, meta=meta)


def _format(
    query: str,
    hits: list[SearchHit],
    *,
    queries: tuple[str, ...],
    evidence: list[PublicEvidence],
) -> str:
    """把 :class:`SearchHit` 列表编码为有明确来源的不可信 JSON 数据。

    Provider 无关——任何 provider 返回的 hits 都经过同一格式化逻辑，
    便于未来加新 provider 时复用 + 视觉一致。

    回答规则属于 system prompt / tool description，不混入无指令权的结果正文。
    JSON 编码也避免第三方 title / snippet 闭合自定义标签伪装外围说明。
    """
    results: list[dict[str, Any]] = []
    for hit, source in zip(hits, evidence, strict=True):
        item: dict[str, Any] = {
            "evidence_ref": source.evidence_ref,
            "title": hit.title,
            "url": source.normalized_url,
            "snippet": hit.snippet[:500],
        }
        if hit.score is not None:
            item["score"] = hit.score
        if hit.published_at is not None:
            item["published_at"] = hit.published_at
        results.append(item)

    payload: dict[str, Any] = {
        "source_type": "web_search",
        "query": query,
        "status": "ok" if hits else "no_results",
        "results": results,
    }
    if len(queries) > 1:
        payload["queries"] = list(queries)
    return json.dumps(payload, ensure_ascii=False, indent=2)


_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TIME_RANGES: set[str] = {"day", "week", "month", "year"}


def _parse_args(
    args: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], WebSearchTimeRange | None]:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    primary = query.strip()

    raw_alternates = args.get("alternate_queries", [])
    if not isinstance(raw_alternates, list) or len(raw_alternates) > 2:
        raise ValueError("alternate_queries must contain at most two strings")
    queries: list[str] = [primary]
    seen_queries = {primary.casefold()}
    for candidate in raw_alternates:
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError("alternate query must be a non-empty string")
        normalized = candidate.strip()
        if normalized.casefold() not in seen_queries:
            seen_queries.add(normalized.casefold())
            queries.append(normalized)

    raw_domains = args.get("include_domains", [])
    if not isinstance(raw_domains, list) or len(raw_domains) > 3:
        raise ValueError("include_domains must contain at most three hostnames")
    domains: list[str] = []
    seen_domains: set[str] = set()
    for index, value in enumerate(raw_domains):
        try:
            domain = _normalize_domain(value)
        except ValueError as exc:
            raise ValueError(
                f"include_domains[{index}] 必须是 hostname（例如 example.com），"
                "不能是站点名称、URL 或路径；请更正或移除这一项"
            ) from exc
        if domain not in seen_domains:
            seen_domains.add(domain)
            domains.append(domain)

    raw_time_range = args.get("time_range")
    if raw_time_range is not None and raw_time_range not in _TIME_RANGES:
        raise ValueError("unsupported time_range")
    time_range = raw_time_range
    return tuple(queries), tuple(domains), time_range


def _normalize_domain(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("domain must be a non-empty string")
    raw = value.strip().rstrip(".")
    if "://" in raw or any(char in raw for char in "/?#@:"):
        raise ValueError("domain must be a hostname, not a URL")
    try:
        domain = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("domain is not valid IDNA") from exc
    labels = domain.split(".")
    if len(labels) < 2 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError("domain must be a public hostname")
    return domain


def _normalized_result_url(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if scheme not in {"http", "https"} or not host:
            raise ValueError
        port = parsed.port
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc = host if port is None or default_port else f"{host}:{port}"
        return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    except (UnicodeError, ValueError):
        return url.strip()

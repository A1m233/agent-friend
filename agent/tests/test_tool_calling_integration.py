"""005 集成测：工具调用循环 happy path / 失败兜底 / 触上限。

覆盖 docs/requirements/005-engine-tool-calling-and-web-search/design.md §2.1
（M5.1 端到端单测覆盖）的三大场景：

- :func:`test_tool_call_happy_path` — LLM 触发 → 执行 → 喂回 → AI 整合回复
- :func:`test_tool_failure_does_not_break_loop` — tool 抛异常时循环不中断
- :func:`test_tool_loop_max_turns_triggers_finalization` — LLM 死循环达上限触发兜底
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from zoneinfo import ZoneInfo

import pytest
from agent.filesystem import FilesystemService, ProtectedPathPolicy
from agent.filesystem.tools import FilesystemOperationTool, RequestFilesystemAccessTool
from agent.latency import RunLatencyContext, monotonic_ms
from agent.system_prompt import IM_QQ_SURFACE
from agent.tool_runtime import ToolRunContext, ToolRunPolicy
from agent.tool_runtime.evidence import evidence_result_meta, make_public_evidence
from agent.tools.builtin.nearby_place_search import (
    AnchorCandidate,
    NearbyPlace,
    NearbyPlaceSearchTool,
    PlaceSort,
)
from agent.tools.builtin.social_platforms import (
    SocialContentType,
    SocialEvidence,
    SocialPlatform,
    SocialPlatformService,
    SocialReadResponse,
    SocialReadTool,
    SocialResolvedUrl,
    SocialSearchRequest,
    SocialSearchResponse,
)
from agent.tools.builtin.web_fetch import ExtractedContent, WebFetchTool
from agent.tools.builtin.web_fetch.url_safety import UntrustedDnsResolutionError
from litellm.exceptions import Timeout as LiteLLMTimeout
from llm_providers.errors import LLMProviderError
from llm_providers.request_witness import witness_from_body
from llm_providers.responses.stream import ResponsesState

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    AttachmentRef,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    ModelResponseStatus,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
    TextDelta,
    Tool,
    ToolCallRequest,
    ToolCallResult,
    ToolRegistry,
    ToolResult,
    TurnDone,
)
from llm_providers import (
    LanguageModel,
    LLMActivity,
    LLMClient,
    LLMEmptyResponseError,
    LLMNetworkError,
    LLMOutputBudgetExhaustedError,
    LLMResponseDiagnostics,
    LLMRetryScheduled,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
    ModelRequest,
    ModelResponse,
    ProviderAssistantTurn,
    ProviderReasoningState,
    ProviderSpec,
    build_provider_spec,
)
from memory import ConversationFragment, MemoryContext

# ---- Fake tools ----


class EchoTool:
    """简单回声 tool：原样返回输入文本。"""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "回声工具：原样返回输入文本，便于测试。"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def __init__(self) -> None:
        self.call_count = 0

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.call_count += 1
        return ToolResult(text=f"echo: {args['message']}")


class FailingTool:
    """总是抛异常的 tool。"""

    name: ClassVar[str] = "fail_tool"
    description: ClassVar[str] = "总是失败的工具，测试错误兜底路径。"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
    }

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        raise RuntimeError("simulated tool crash")


class UserActionRequiredTool:
    name: ClassVar[str] = "user_action_required"
    description: ClassVar[str] = "需要用户先完成外部动作。"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            text='{"error":"setup_required"}',
            is_error=True,
            meta={"error_kind": "setup_required", "requires_user_action": True},
        )


class ApprovalDeniedTool:
    name: ClassVar[str] = "approval_denied"
    description: ClassVar[str] = "模拟用户拒绝后未执行的工具。"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            text='{"error":"approval_denied"}',
            is_error=True,
            meta={
                "error_kind": "approval_denied",
                "executed": False,
                "authorization_requested": True,
            },
        )


class SpyRecallTool:
    """记录 ``recall_past_chats`` 的真实 invoke 入参，验证隐藏上下文注入。"""

    name: ClassVar[str] = "recall_past_chats"
    description: ClassVar[str] = "回忆过去和这位用户聊过的事。"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
    }

    def __init__(self) -> None:
        self.seen_args: list[dict[str, Any]] = []

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.seen_args.append(dict(args))
        return ToolResult(text="找到一些旧聊天。")


class SpySocialSearchTool:
    """记录 social_search 的真实入参，验证 session id 只在执行期注入。"""

    name: ClassVar[str] = "social_search"
    description: ClassVar[str] = "搜索平台内容。"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "platform": {"type": "string"},
            "query": {"type": "string"},
        },
        "required": ["platform", "query"],
    }

    def __init__(self) -> None:
        self.seen_args: list[dict[str, Any]] = []

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.seen_args.append(dict(args))
        return ToolResult(text='{"status":"no_results"}')


@dataclass(frozen=True)
class _DirectBilibiliLocator:
    platform: SocialPlatform = "bilibili"
    content_type: SocialContentType = "article"
    opus_id: int = 1201845223737524246


class _DirectBilibiliConnector:
    platform: ClassVar[SocialPlatform] = "bilibili"
    name: ClassVar[str] = "direct-bilibili-fixture"
    version: ClassVar[str] = "1"
    canonical_url: ClassVar[str] = "https://www.bilibili.com/opus/1201845223737524246"

    def __init__(self) -> None:
        self.read_calls = 0

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        del request
        raise AssertionError("direct URL must not fall back to broad platform search")

    def resolve_url(self, url: str) -> SocialResolvedUrl | None:
        if url.split("?", 1)[0].rstrip("/") != self.canonical_url:
            return None
        return SocialResolvedUrl(
            platform=self.platform,
            content_type="article",
            canonical_url=self.canonical_url,
            locator=_DirectBilibiliLocator(),
        )

    def read(self, locator: object) -> SocialReadResponse:
        assert locator == _DirectBilibiliLocator()
        self.read_calls += 1
        return SocialReadResponse(
            title="受控 Opus",
            author="fixture-author",
            canonical_url=self.canonical_url,
            evidence=(SocialEvidence(kind="article_body", text="受控 Opus 正文"),),
        )

    def close(self) -> None:
        pass


class SpySocialAuthRequestTool:
    """记录授权请求的可信 origin 注入，不执行任何登录动作。"""

    name: ClassVar[str] = "request_social_auth"
    description: ClassVar[str] = "创建等待电脑端确认的登录请求。"
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "platform": {"type": "string"},
            "missing_capabilities": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["platform", "missing_capabilities", "reason"],
    }

    def __init__(self) -> None:
        self.seen_args: list[dict[str, Any]] = []

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.seen_args.append(dict(args))
        return ToolResult(text='{"status":"pending_user_authorization"}')


class SwitchModelTool:
    name: ClassVar[str] = "switch_model_for_test"
    description: ClassVar[str] = "切换测试会话模型。"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.conversation: Conversation | None = None

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        assert self.conversation is not None
        self.conversation.switch_model("kimi/kimi-k2.6")
        return ToolResult(text="switched")


class NearbyPlaceProviderStub:
    """为 Conversation 集成测试提供确定性的地点结果。"""

    name: ClassVar[str] = "stub"

    def search_anchors(self, *, near: str, city: str | None) -> tuple[AnchorCandidate, ...]:
        return (
            AnchorCandidate(
                ref="anchor-1",
                name="福民银行",
                address="广东省深圳市福田区福民路1号",
                city="深圳市",
                district="福田区",
                longitude=114.055,
                latitude=22.521,
            ),
        )

    def get_anchor(self, *, ref: str) -> AnchorCandidate | None:
        return None

    def search_nearby(
        self,
        *,
        query: str,
        anchor: AnchorCandidate,
        radius_meters: int,
        sort: PlaceSort,
        limit: int,
    ) -> tuple[NearbyPlace, ...]:
        return (
            NearbyPlace(
                name="证件照相馆",
                distance_meters=320,
                address="广东省深圳市福田区福强路2号",
            ),
        )


# ---- Scripted fake LLM ----


@dataclass
class _ScriptedLLMClient:
    """按脚本（每轮一个 LLMStreamEvent 列表）依次回放 stream 输出。

    每次调 :meth:`stream` 消费 ``self.script`` 列表里的下一项。``self.received``
    保留每轮收到的 messages 用于断言。
    """

    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    turn_idx: int = 0
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    received_overrides: list[dict[str, Any]] = field(default_factory=list)
    received_tools: list[list[dict[str, Any]] | None] = field(default_factory=list)
    received_cache_keys: list[str | None] = field(default_factory=list)
    context_window: int = 128000  # 009：Conversation 经此推导预算阈值

    def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(request.messages)
        self.received_overrides.append(dict(request.settings))
        self.received_tools.append(request.tools)
        self.received_cache_keys.append(request.cache_key)
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


@dataclass
class _WitnessingLLMClient:
    """Build final-wire witnesses while returning deterministic no-tool turns."""

    received: list[ModelRequest] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    diagnostics: list[LLMResponseDiagnostics] = field(default_factory=list)
    transform_identities: list[str] = field(default_factory=lambda: ["fake:v1"])
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        return ModelResponse(text="")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.received.append(request)
        body = json.dumps(
            {
                "messages": request.messages,
                "tools": request.tools or [],
                "settings": dict(request.settings),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        witness = witness_from_body(
            body,
            provider_id="deterministic-test",
            transport="fake",
            transform_identity=self.transform_identities[
                min(len(self.received) - 1, len(self.transform_identities) - 1)
            ],
        )
        domains = request.control.transcript_item_domains
        if domains is not None and len(domains) == len(witness.transcript_item_digests):
            witness = replace(
                witness,
                transcript_item_domains=domains,
                contributor_mapping_precision="exact_item",
            )
        previous = request.control.previous_request_witness
        relation = witness.relation_to(previous)
        self.relations.append(relation)
        diagnostics = LLMResponseDiagnostics(
            transport="fake",
            request_bytes=witness.request_bytes,
            request_fingerprint=witness.request_fingerprint,
            request_relation=relation,
            common_prefix_items=witness.common_prefix_items(previous),
            previous_request_items=(
                len(previous.transcript_item_digests) if previous is not None else None
            ),
            first_divergence_index=witness.first_divergence_index(previous),
            first_divergence_role=witness.first_divergence_role(previous),
            first_divergence_domain=witness.first_divergence_domain(previous),
            first_divergence_previous_domain=(witness.first_divergence_previous_domain(previous)),
            first_divergence_current_domain=(witness.first_divergence_current_domain(previous)),
            contributor_mapping_precision=witness.contributor_mapping_precision,
            lost_prefix_items=witness.lost_prefix_items(previous),
            request_witness=witness,
            response_model="deterministic-test-model",
        )
        self.diagnostics.append(diagnostics)
        answer = f"answer-{len(self.received)}"
        yield LLMTextDelta(text=answer)
        yield LLMTurnDone(
            stop_reason="end_turn",
            usage=LLMUsage(
                prompt_tokens=100 * len(self.received),
                completion_tokens=5,
                total_tokens=100 * len(self.received) + 5,
            ),
            diagnostics=diagnostics,
            assistant_turn=ProviderAssistantTurn(content=answer),
        )


@dataclass
class _RecoveringResponseClient:
    failures: list[Exception]
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    calls: int = 0
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        return ModelResponse(text="")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.received.append(request.messages)
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        yield LLMTextDelta(text="recovered")
        yield LLMTurnDone(stop_reason="end_turn")


class _DynamicMemory:
    """按当前 query 返回不同记忆，验证召回进入 append-only model history。"""

    def observe(self, fragment: ConversationFragment) -> None:
        del fragment

    def retrieve(self, query: str, **kwargs: Any) -> MemoryContext:
        del kwargs
        return MemoryContext(rendered=f"memory-for:{query}", items=[])


@dataclass
class _SequencedMemory:
    contexts: list[MemoryContext]

    def observe(self, fragment: ConversationFragment) -> None:
        del fragment

    def retrieve(self, query: str, **kwargs: Any) -> MemoryContext:
        del query, kwargs
        return self.contexts.pop(0)


def _make_conversation(
    tmp_path: Path,
    *,
    fake_llm: LanguageModel,
    registry: ToolRegistry | None = None,
    llm_client_factory: Any | None = None,
    tool_run_policy: ToolRunPolicy | None = None,
    tool_run_context_factory: Any | None = None,
    time_provider: Any | None = None,
    memory: Any | None = None,
    model: str = "deepseek/deepseek-chat",
    reasoning_preset: str | None = None,
) -> Conversation:
    """组装最小可跑的 :class:`Conversation`：fake LLM + 真 catalog + 内存内 store。"""
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t",
        persona="default",
        model=model,
        reasoning_preset=reasoning_preset,
        persona_id=persona_id,
    )
    store.create(session)
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    def builder_factory(pid: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=fake_llm,
        llm_client_factory=llm_client_factory,
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        tool_registry=registry,
        tool_run_policy=tool_run_policy,
        tool_run_context_factory=tool_run_context_factory,
        time_provider=time_provider,
        memory=memory,
    )


@pytest.mark.parametrize(
    ("failure", "instruction_fragment"),
    [
        (LLMEmptyResponseError("empty"), "没有产生任何可用响应项"),
        (LLMOutputBudgetExhaustedError("budget"), "输出预算耗尽在不可见推理中"),
    ],
)
def test_incomplete_model_response_recovers_with_same_run(
    tmp_path: Path,
    failure: Exception,
    instruction_fragment: str,
) -> None:
    fake = _RecoveringResponseClient(failures=[failure])
    conv = _make_conversation(tmp_path, fake_llm=fake)

    events = list(conv.stream("recover response"))

    assert fake.calls == 2
    assert any(
        isinstance(event, ModelResponseStatus) and event.phase == "retrying" for event in events
    )
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "recovered"
    assert any(
        message.get("role") == "user" and instruction_fragment in str(message.get("content", ""))
        for message in fake.received[1]
    )
    assistants = [event for event in conv.session.events if event.type == "assistant_message"]
    assert [event.payload["content"] for event in assistants] == ["recovered"]


@pytest.mark.parametrize("empty_at", [1, 5, 6])
def test_transport_and_empty_response_repair_share_one_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, empty_at: int
) -> None:
    client = LLMClient(
        ProviderSpec(model="deepseek/deepseek-chat", api_key="test"),
        network_retry_backoff_seconds=0,
    )
    calls = 0

    def attempt(*args: Any, **kwargs: Any) -> Iterator[LLMStreamEvent]:
        nonlocal calls
        calls += 1
        if calls == empty_at:
            raise LLMEmptyResponseError("injected empty response")
        raise LLMNetworkError("injected disconnect")
        yield  # pragma: no cover

    monkeypatch.setattr(client, "_stream_once", attempt)
    conv = _make_conversation(tmp_path, fake_llm=client)
    events: list[Any] = []
    with pytest.raises((LLMNetworkError, LLMEmptyResponseError)):
        for item in conv.stream("finish"):
            events.append(item)
    assert calls == 6
    assert [
        (item.attempt, item.max_attempts)
        for item in events
        if isinstance(item, ModelResponseStatus) and item.phase == "retrying"
    ] == [(i, 6) for i in range(2, 7)]


def test_incomplete_model_response_fails_after_explicit_recovery_budget(
    tmp_path: Path,
) -> None:
    fake = _RecoveringResponseClient(
        failures=[LLMEmptyResponseError("first"), LLMEmptyResponseError("second")]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    with pytest.raises(LLMEmptyResponseError, match="second"):
        list(conv.stream("recover response"))

    assert fake.calls == 2
    assert not any(event.type == "assistant_message" for event in conv.session.events)


def test_memory_recall_stays_append_only_after_its_user_turn(
    tmp_path: Path,
) -> None:
    fake = _WitnessingLLMClient()
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=_DynamicMemory())

    list(conv.stream("first"))
    list(conv.stream("second"))

    first_model_request, second_model_request = fake.received
    first_request = first_model_request.messages
    second_request = second_model_request.messages
    first_user_index = next(
        index for index, message in enumerate(first_request) if message.get("content") == "first"
    )
    first_memory_index = next(
        index
        for index, message in enumerate(first_request)
        if "memory-for:first" in str(message.get("content") or "")
    )
    assert first_user_index < first_memory_index

    second_user_index = next(
        index for index, message in enumerate(second_request) if message.get("content") == "second"
    )
    second_memory_index = next(
        index
        for index, message in enumerate(second_request)
        if "memory-for:second" in message.get("content", "")
    )
    assert second_user_index < second_memory_index
    assert second_request[: len(first_request)] == first_request
    assert any(
        "memory-for:first" in str(message.get("content") or "") for message in second_request
    )
    assert [message.content for message in conv.session.messages if message.role == "user"] == [
        "first",
        "second",
    ]
    assert first_model_request.control.transcript_item_domains is not None
    assert first_model_request.control.transcript_item_domains[first_memory_index] == "memory"
    assert second_model_request.control.transcript_item_domains is not None
    assert second_model_request.control.transcript_item_domains[second_memory_index] == "memory"
    assert fake.diagnostics[1].request_relation == "strict_extension"
    assert fake.diagnostics[1].first_divergence_domain != "runtime"
    comparison = second_model_request.control.trace_context
    assert comparison is not None and comparison.comparison is not None
    flags = comparison.comparison["change_flags"]
    assert flags["memory_content_changed"] is True
    assert flags["memory_position_changed"] is False


def test_memory_recall_presence_content_and_relative_position_are_diagnosed(
    tmp_path: Path,
) -> None:
    fake = _WitnessingLLMClient()
    memory = _SequencedMemory(
        [
            MemoryContext.empty(),
            MemoryContext(rendered="memory-a", items=[]),
            MemoryContext(rendered="memory-a", items=[]),
            MemoryContext(rendered="memory-b", items=[]),
            MemoryContext.empty(),
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, memory=memory)

    for query in ("none", "first", "same", "changed", "gone"):
        list(conv.stream(query))

    requests = [request.messages for request in fake.received]
    memory_counts = [
        sum("memory-a" in str(item.get("content") or "") for item in request)
        + sum("memory-b" in str(item.get("content") or "") for item in request)
        for request in requests
    ]
    assert memory_counts == [0, 1, 2, 3, 3]
    assert all(current[: len(previous)] == previous for previous, current in pairwise(requests))
    for query, request_messages in zip(("first", "same", "changed"), requests[1:4], strict=True):
        user_index = next(
            index for index, item in enumerate(request_messages) if item["content"] == query
        )
        latest_memory_index = max(
            index
            for index, item in enumerate(request_messages)
            if "memory-" in str(item.get("content") or "")
        )
        assert latest_memory_index > user_index

    comparisons = []
    for model_request in fake.received:
        trace_context = model_request.control.trace_context
        assert trace_context is not None and trace_context.comparison is not None
        comparisons.append(trace_context.comparison)
    assert comparisons[1]["change_flags"]["memory_presence_changed"] is True
    assert comparisons[2]["change_flags"]["memory_presence_changed"] is False
    assert comparisons[2]["change_flags"]["memory_content_changed"] is False
    assert comparisons[2]["change_flags"]["memory_position_changed"] is False
    assert comparisons[3]["change_flags"]["memory_content_changed"] is True
    assert comparisons[3]["change_flags"]["memory_position_changed"] is False
    assert comparisons[4]["change_flags"]["memory_presence_changed"] is True


def test_provider_transform_rebase_starts_a_diagnostic_cache_epoch(tmp_path: Path) -> None:
    fake = _WitnessingLLMClient(transform_identities=["fake:v1", "fake:v2"])
    conv = _make_conversation(tmp_path, fake_llm=fake)

    list(conv.stream("first"))
    list(conv.stream("second"))

    assert fake.diagnostics[1].request_relation == "rebase"
    assert conv.cache_epoch_diagnostics["generation"] == 1
    assert conv.cache_epoch_diagnostics["reason"] == "provider_transform_rebase"
    assistant_events = [event for event in conv.session.events if event.type == "assistant_message"]
    cache_diagnostics = assistant_events[-1].meta["context_usage"]["cache_diagnostics"]
    assert cache_diagnostics["continuation_disposition"] == "rebuilt"
    assert cache_diagnostics["rebase_reason"] == "provider_transform_rebase"


def test_turn_uses_stable_provider_neutral_cache_key(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [LLMTextDelta(text="one"), LLMTurnDone(stop_reason="end_turn")],
            [LLMTextDelta(text="two"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        model="kimi/kimi-k2.6",
    )

    list(conv.stream("first"))
    list(conv.stream("second"))

    assert fake.received_cache_keys == [
        conv.session.session_id,
        conv.session.session_id,
    ]


def test_consecutive_no_tool_user_turns_bind_cross_turn_final_wire_lineage(
    tmp_path: Path,
) -> None:
    fake = _WitnessingLLMClient()
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        model="kimi/kimi-k2.6",
    )

    list(conv.stream("first", run_id="run-one"))
    list(conv.stream("second", run_id="run-two"))

    assert len(fake.received) == 2
    first_request, second_request = fake.received
    assert first_request.control.previous_request_witness is None
    assert second_request.control.previous_request_witness is not None
    trace = second_request.control.trace_context
    assert trace is not None
    assert trace.comparison_scope == "cross_user_turn"
    assert trace.comparison_precision == "exact_item"
    assert fake.relations[1] != "baseline"
    comparison = dict(trace.comparison or {})
    flags = dict(comparison.get("change_flags") or {})
    if fake.relations[1] == "strict_extension":
        assert flags["messages_appended"] is True
        assert flags["messages_rewritten"] is False
    else:
        assert fake.relations[1] == "rebase"
        assert fake.diagnostics[1].first_divergence_domain is not None
        assert fake.diagnostics[1].lost_prefix_items is not None


def test_conversation_restart_restores_only_domain_level_cross_turn_lineage(
    tmp_path: Path,
) -> None:
    first_llm = _WitnessingLLMClient()
    first = _make_conversation(
        tmp_path,
        fake_llm=first_llm,
        model="kimi/kimi-k2.6",
    )
    secret = "PRIVATE_RESTART_PROMPT"
    list(first.stream(secret, run_id="run-one"))

    assistant = next(
        event for event in reversed(first.session.events) if event.type == "assistant_message"
    )
    cache_diagnostics = assistant.meta["context_usage"]["cache_diagnostics"]
    encoded = json.dumps(cache_diagnostics, ensure_ascii=False)
    assert secret not in encoded
    assert "message_digests" not in encoded
    assert "transcript_item_digests" not in encoded

    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    loaded = store.load(first.session.session_id)
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    builder = MarkdownPromptBuilder(persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog)
    second_llm = _WitnessingLLMClient()
    restarted = Conversation(
        session=loaded,
        store=store,
        llm_client=second_llm,
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
            persona_id=persona_id,
            catalog=catalog,
        ),
        catalog=catalog,
    )

    list(restarted.stream("after restart", run_id="run-two"))

    request = second_llm.received[0]
    trace = request.control.trace_context
    assert trace is not None
    assert trace.comparison_scope == "cross_process_resume"
    assert trace.comparison_precision == "domain_only"
    assert request.control.previous_request_witness is None
    comparison = dict(trace.comparison or {})
    assert comparison["first_divergence_index"] is None
    assert comparison["first_divergence_role"] is None
    assert comparison["coarse_relation"] in {
        "coarse_domain_change",
        "unavailable_precision_reduced",
    }


def test_cache_key_does_not_leak_into_provider_settings(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="ok"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    list(conv.stream("first"))

    assert "prompt_cache_key" not in fake.received_overrides[0]
    assert fake.received_cache_keys == [conv.session.session_id]


def test_provider_reasoning_state_persists_privately_and_replays_for_tools(
    tmp_path: Path,
) -> None:
    first_state = ProviderReasoningState(
        provider_id="deepseek",
        model_id="deepseek/deepseek-v4-flash",
        format="reasoning_content",
        replay_scope="conversation",
        payload="private-thought-one",
    )
    second_state = ProviderReasoningState(
        provider_id="deepseek",
        model_id="deepseek/deepseek-v4-flash",
        format="reasoning_content",
        replay_scope="conversation",
        payload="private-thought-two",
    )
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="echo-1",
                    tool_name="echo",
                    args_json_delta='{"message":"hello"}',
                ),
                LLMTurnDone(stop_reason="tool_use", reasoning_state=first_state),
            ],
            [
                LLMTextDelta(text="first answer"),
                LLMTurnDone(stop_reason="end_turn", reasoning_state=second_state),
            ],
            [LLMTextDelta(text="second answer"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([EchoTool()]),
        model="deepseek/deepseek-v4-flash",
        reasoning_preset="high",
    )

    list(conv.stream("first", run_id="run-one"))
    list(conv.stream("second", run_id="run-two"))

    tool_continuation_assistants = [
        message
        for message in fake.received[1]
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert tool_continuation_assistants[-1]["reasoning_content"] == "private-thought-one"
    replayed = [
        message.get("reasoning_content")
        for message in fake.received[2]
        if message.get("role") == "assistant" and message.get("reasoning_content")
    ]
    assert replayed == ["private-thought-one", "private-thought-two"]
    assert (
        len([event for event in conv.session.events if event.type == "provider_reasoning_state"])
        == 2
    )
    assert all("provider_reasoning_state" not in message.meta for message in conv.session.messages)


def test_tool_loop_freezes_actual_llm_client_and_model_metadata(tmp_path: Path) -> None:
    old_client = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="switch-1",
                    tool_name="switch_model_for_test",
                    args_json_delta="{}",
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="finished on old client"), LLMTurnDone(stop_reason="end_turn")],
        ],
        context_window=128000,
    )
    new_client = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="wrong client"), LLMTurnDone(stop_reason="end_turn")]],
        context_window=16000,
    )
    switch_tool = SwitchModelTool()
    conv = _make_conversation(
        tmp_path,
        fake_llm=old_client,
        registry=ToolRegistry([switch_tool]),
        llm_client_factory=lambda _model: new_client,
    )
    switch_tool.conversation = conv

    events = list(conv.stream("switch during this run", run_id="run-snapshot"))

    assert any(
        isinstance(event, TextDelta) and event.text == "finished on old client" for event in events
    )
    assert len(old_client.received) == 2
    assert new_client.received == []
    assert conv.current_model == "kimi/kimi-k2.6"
    assistant_events = [event for event in conv.session.events if event.type == "assistant_message"]
    assert assistant_events
    assert {event.meta["model"] for event in assistant_events} == {"deepseek/deepseek-chat"}


def test_switch_model_factory_failure_is_atomic(tmp_path: Path) -> None:
    old_client = _ScriptedLLMClient()

    def unavailable_factory(_model: str) -> LanguageModel:
        raise RuntimeError("credential unavailable")

    conv = _make_conversation(
        tmp_path,
        fake_llm=old_client,
        llm_client_factory=unavailable_factory,
    )

    with pytest.raises(RuntimeError, match="credential unavailable"):
        conv.switch_model("kimi/kimi-k2.6")

    assert conv.current_model == "deepseek/deepseek-chat"
    assert not any(event.type == "model_change" for event in conv.session.events)


def _tool_call_chunk(call_id: str, message: str) -> SimpleNamespace:
    return SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id=call_id,
                            function=SimpleNamespace(
                                name="echo",
                                arguments=json.dumps({"message": message}),
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
    )


def _text_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=text, tool_calls=[]),
                finish_reason="stop",
            )
        ],
    )


# ===== happy path =====


def test_hidden_reasoning_activity_becomes_transient_model_status(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMActivity(kind="reasoning"),
                LLMTextDelta(text="完成"),
                LLMTurnDone(stop_reason="end_turn"),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)

    events = list(conv.stream("hi"))

    statuses = [event for event in events if isinstance(event, ModelResponseStatus)]
    assert statuses[0].phase == "waiting"
    assert statuses[0].after_tool is False
    assert any(status.phase == "reasoning" for status in statuses)
    assert all("reasoning" not in event.type for event in conv.session.events)


def test_tool_call_happy_path(tmp_path: Path) -> None:
    """LLM 第一轮调 echo tool → 工具执行 → LLM 第二轮整合结果输出 → 结束。"""
    echo = EchoTool()
    registry = ToolRegistry([echo])

    fake = _ScriptedLLMClient(
        script=[
            # turn 0: LLM 要求调 echo
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call_1",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "hi"}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            # turn 1: LLM 收到 tool 结果，整合输出
            [
                LLMTextDelta(text="基于 echo 结果："),
                LLMTextDelta(text="echo: hi"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)

    events = list(conv.stream("hi"))

    text_deltas = [ev for ev in events if isinstance(ev, TextDelta)]
    tool_reqs = [ev for ev in events if isinstance(ev, ToolCallRequest)]
    tool_results = [ev for ev in events if isinstance(ev, ToolCallResult)]
    dones = [ev for ev in events if isinstance(ev, TurnDone)]

    assert len(tool_reqs) == 1
    assert tool_reqs[0].tool_name == "echo"
    assert tool_reqs[0].tool_call_id == "call_1"
    assert tool_reqs[0].args == {"message": "hi"}

    assert len(tool_results) == 1
    assert tool_results[0].is_error is False
    assert tool_results[0].text == "echo: hi"
    assert tool_results[0].tool_call_id == "call_1"

    assert "".join(t.text for t in text_deltas) == "基于 echo 结果：echo: hi"

    assert len(dones) == 1
    assert dones[0].stop_reason == "end_turn"
    assert dones[0].total_tool_calls == 1

    assert echo.call_count == 1
    assert any(
        isinstance(event, ModelResponseStatus) and event.phase == "waiting" and event.after_tool
        for event in events
    )

    # 纯 tool-call 轮由 tool_call_request 表达，不额外落空 assistant；
    # 最终文本轮才产生 assistant_message。
    types = [ev.type for ev in conv.session.events]
    assert types[0] == "session_meta"
    assert types.count("user_message") == 1
    assert types.count("assistant_message") == 1
    assert types.count("tool_call_request") == 1
    assert types.count("tool_call_result") == 1

    # 第二轮 messages 应包含 tool message（让 LLM 看到结果）
    second_turn_messages = fake.received[1]
    tool_role_msgs = [m for m in second_turn_messages if m.get("role") == "tool"]
    assert len(tool_role_msgs) == 1
    assert tool_role_msgs[0]["content"] == "echo: hi"
    assert tool_role_msgs[0]["tool_call_id"] == "call_1"

    # 第二轮 messages 中 assistant 消息应携带 tool_calls 字段
    assistant_msgs = [m for m in second_turn_messages if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert "tool_calls" in assistant_msgs[0]
    assert assistant_msgs[0]["tool_calls"][0]["id"] == "call_1"


@pytest.mark.parametrize("broken_args", ["{", "{}"])
def test_malformed_tool_call_is_closed_as_failed_item_before_recovery(
    tmp_path: Path,
    broken_args: str,
) -> None:
    """Provider 损坏或 schema 不合法的参数不得变成空授权卡。"""
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="broken-call",
                    tool_name="echo",
                    args_json_delta=broken_args,
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="recovered-call",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "fixed"}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([echo]),
    )

    events = list(conv.stream("recover malformed tool args"))

    requests = [event for event in events if isinstance(event, ToolCallRequest)]
    assert [(event.tool_call_id, event.args) for event in requests] == [
        ("broken-call", {}),
        ("recovered-call", {"message": "fixed"}),
    ]
    assert requests[0].execution_disposition == "rejected_before_execution"
    assert requests[1].execution_disposition == "execute"
    assert echo.call_count == 1
    assert len(fake.received) == 3
    tool_results = [event for event in events if isinstance(event, ToolCallResult)]
    assert len(tool_results) == 2
    assert tool_results[0].is_error is True
    assert tool_results[0].executed is False
    assert tool_results[0].authorization_requested is False
    assert tool_results[1].is_error is False
    assert tool_results[1].executed is True
    assert any(message.get("role") == "tool" for message in fake.received[1])
    if broken_args == "{}":
        assert any(
            "缺少必填参数：message" in str(message.get("content") or "")
            for message in fake.received[1]
        )
    assistant_events = [event for event in conv.session.events if event.type == "assistant_message"]
    assert [event.payload["content"] for event in assistant_events] == ["done"]


def test_tool_result_execution_metadata_is_preserved_in_stream_and_session(
    tmp_path: Path,
) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="approval-call",
                    tool_name="approval_denied",
                    args_json_delta="{}",
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="stopped"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([ApprovalDeniedTool()]),
    )

    events = list(conv.stream("reject it"))

    result = next(event for event in events if isinstance(event, ToolCallResult))
    assert result.executed is False
    assert result.authorization_requested is True
    persisted = next(event for event in conv.session.events if event.type == "tool_call_result")
    assert persisted.payload["executed"] is False
    assert persisted.payload["authorization_requested"] is True


def test_repeated_malformed_tool_calls_keep_recovering_until_valid_call(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    echo = EchoTool()
    sensitive_marker = "credential-value-must-not-appear"
    broken_args = json.dumps({"message": [sensitive_marker]})
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "broken-1", "echo", broken_args),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(0, "broken-2", "echo", broken_args),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(
                    0,
                    "fixed",
                    "echo",
                    json.dumps({"message": "fixed"}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([echo]),
    )
    events: list[Any] = []
    caplog.set_level(logging.WARNING)

    events.extend(conv.stream("repeat malformed tool args", run_id="run-malformed"))

    requests = [event for event in events if isinstance(event, ToolCallRequest)]
    results = [event for event in events if isinstance(event, ToolCallResult)]
    assert len(requests) == len(results) == 3
    assert [event.executed for event in results] == [False, False, True]
    assert echo.call_count == 1
    assert len(fake.received) == 4
    assert next(event for event in events if isinstance(event, TurnDone)).stop_reason == "end_turn"
    diagnostic_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "run_id=run-malformed" in diagnostic_text
    assert '"provided_known_fields": ["message"]' in diagnostic_text
    assert '"known_field_types": {"message": "array"}' in diagnostic_text
    assert sensitive_marker not in diagnostic_text
    assert all(
        "运行边界" not in str(message.get("content", ""))
        for turn in fake.received
        for message in turn
    )


def test_mixed_tool_batch_executes_valid_item_and_closes_rejected_item(tmp_path: Path) -> None:
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "invalid", "echo", "{}"),
                LLMToolCallDelta(1, "valid", "echo", '{"message":"ok"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([echo]))

    events = list(conv.stream("mixed batch"))

    results = [event for event in events if isinstance(event, ToolCallResult)]
    assert [(event.tool_call_id, event.is_error, event.executed) for event in results] == [
        ("invalid", True, False),
        ("valid", False, True),
    ]
    assert echo.call_count == 1
    done = next(event for event in events if isinstance(event, TurnDone))
    assert done.stop_reason == "end_turn"
    assert done.total_tool_calls == 1


def test_unbindable_tool_output_becomes_model_visible_rejection_without_tool_card(
    tmp_path: Path,
) -> None:
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "", "echo", '{"message":"missing id"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(0, "fixed", "echo", '{"message":"fixed"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([echo]))

    events = list(conv.stream("recover missing identity"))

    requests = [event for event in events if isinstance(event, ToolCallRequest)]
    assert [event.tool_call_id for event in requests] == ["fixed"]
    rejection = next(
        event for event in conv.session.events if event.type == "model_output_rejection"
    )
    assert rejection.payload["error_code"] == "missing_tool_identity"
    assert any(
        message.get("role") == "system" and "<model_output_rejection>" in message.get("content", "")
        for message in fake.received[1]
    )
    assert echo.call_count == 1


def test_normal_system_turn_reuses_full_tool_loop(tmp_path: Path) -> None:
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="reminder-tool",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "reminder"}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="处理完成"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([echo]),
    )

    events = list(
        conv.dispatch_system_turn(
            source_kind="todo_reminder",
            system_prompt_addendum='<system_events>\n[{"type":"todo_reminder"}]\n</system_events>',
            presentation="conversation",
            tool_mode="normal",
            channel="text",
            surface=IM_QQ_SURFACE,
            interaction_destination_id="qq|bind|c2c|chat|session",
        )
    )

    assert echo.call_count == 1
    assert any(isinstance(event, ToolCallRequest) for event in events)
    assert any(isinstance(event, ToolCallResult) for event in events)
    done = next(event for event in events if isinstance(event, TurnDone))
    assert done.total_tool_calls == 1
    assert [event.type for event in conv.session.events].count("system_trigger") == 1
    assert all(event.type != "user_message" for event in conv.session.events)
    assert fake.received_tools[0] is not None


def test_default_system_turn_keeps_tools_disabled(tmp_path: Path) -> None:
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[[LLMTextDelta(text="普通主动回应"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([echo]),
    )

    list(
        conv.dispatch_system_turn(
            source_kind="cron:bedtime",
            system_prompt_addendum="该休息了",
        )
    )

    assert echo.call_count == 0
    assert fake.received_tools == [None]


def test_run_latency_logs_stitch_llm_cache_tool_and_first_text_without_content(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="trace-call",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "PRIVATE_TOOL_ARG"}),
                ),
                LLMTurnDone(
                    stop_reason="tool_use",
                    usage=LLMUsage(
                        prompt_tokens=100,
                        completion_tokens=3,
                        total_tokens=103,
                        cached_prompt_tokens=80,
                        cache_miss_prompt_tokens=20,
                    ),
                ),
            ],
            [
                LLMTextDelta(text="PRIVATE_ANSWER"),
                LLMTurnDone(
                    stop_reason="end_turn",
                    usage=LLMUsage(
                        prompt_tokens=200,
                        completion_tokens=4,
                        total_tokens=204,
                        cached_prompt_tokens=190,
                        cache_miss_prompt_tokens=10,
                    ),
                ),
            ],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([echo]),
    )
    trace = RunLatencyContext(
        run_id="run-latency",
        session_id=conv.session.session_id,
        started_ms=monotonic_ms(),
    )
    conv.set_run_latency_context(trace)
    caplog.set_level(logging.INFO, logger="agent.conversation")

    list(conv.stream("PRIVATE_USER_TEXT", run_id="run-latency"))

    latency_text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("run_latency ")
    )
    assert "event=context_build_done" in latency_text
    assert "event=llm_stream_start" in latency_text
    assert "event=llm_request_start" in latency_text
    assert "call_index=1 call_kind=initial" in latency_text
    assert "event_kind=tool_call_delta" in latency_text
    assert "event=tool_batch_done" in latency_text
    assert "call_index=2 call_kind=tool_continuation" in latency_text
    assert "event=run_first_text_delta" in latency_text
    assert "cached_prompt_tokens=190" in latency_text
    assert "cache_miss_prompt_tokens=10" in latency_text
    assert "cache_hit_ratio=0.9500" in latency_text
    assert "cache_reporting_state=reported_nonzero" in latency_text
    # 工具续轮会刷新逐次可信时间，因此上一轮完整 request prefix 不再保持不变。
    assert "previous_request_prefix_preserved=true" in latency_text
    assert "continuation_disposition=continued" in latency_text
    assert "tool_result_chars=" in latency_text
    assert trace.llm_call_count == 2
    assert trace.tool_call_count == 1
    assert trace.first_text_elapsed_ms is not None
    assert "PRIVATE_USER_TEXT" not in latency_text
    assert "PRIVATE_TOOL_ARG" not in latency_text
    assert "PRIVATE_ANSWER" not in latency_text
    diagnostic_events = [event for event in conv.session.events if "context_usage" in event.meta]
    cache_diagnostics = [
        event.meta.get("context_usage", {}).get("cache_diagnostics", {})
        for event in diagnostic_events
    ]
    assert cache_diagnostics[0]["cache_lineage"] == "new"
    assert cache_diagnostics[1]["previous_request_prefix_preserved"] is True
    assert cache_diagnostics[1]["cache_state"] == "reported_nonzero"
    assert "PRIVATE_USER_TEXT" not in json.dumps(cache_diagnostics, ensure_ascii=False)


def test_nearby_place_search_uses_existing_tool_event_loop(tmp_path: Path) -> None:
    """真实地点工具经通用事件链执行，并把范围证据交回模型整合。"""
    tool = NearbyPlaceSearchTool(NearbyPlaceProviderStub())
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="nearby-1",
                    tool_name="nearby_place_search",
                    args_json_delta=json.dumps(
                        {
                            "query": "照相馆",
                            "near": "福民银行",
                            "city": "深圳",
                            "sort": "distance",
                        }
                    ),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMTextDelta(
                    text=(
                        "以福民银行为中心 5 公里内，最近的是证件照相馆，约 320 米，"
                        "完整地址为广东省深圳市福田区福强路2号。"
                    )
                ),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([tool]))

    events = list(conv.stream("帮我找福民银行附近最近的照相点"))

    result = next(event for event in events if isinstance(event, ToolCallResult))
    payload = json.loads(result.text)
    assert result.is_error is False
    assert payload["status"] == "success"
    assert payload["search_area"]["description"] == "以福民银行为中心 5 公里内"
    assert payload["places"][0]["distance_meters"] == 320
    assert [event.type for event in conv.session.events].count("tool_call_result") == 1

    tool_message = next(message for message in fake.received[1] if message.get("role") == "tool")
    assert json.loads(tool_message["content"])["search_area"]["radius_meters"] == 5000
    assert "5 公里内" in "".join(event.text for event in events if isinstance(event, TextDelta))
    assert "广东省深圳市福田区福强路2号" in "".join(
        event.text for event in events if isinstance(event, TextDelta)
    )


@pytest.mark.parametrize("completed_first_call", [False, True])
def test_responses_protocol_failure_never_executes_partial_or_repeats_tools(
    tmp_path: Path, completed_first_call: bool
) -> None:
    echo = EchoTool()

    @dataclass
    class ProtocolClient:
        calls: int = 0
        context_window: int = 128000

        def generate(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(text="")

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            self.calls += 1
            parser = ResponsesState(build_provider_spec(provider_id="deepseek", api_key="fixture"))
            item = {
                "id": "fc-1",
                "type": "function_call",
                "call_id": f"call-{self.calls}",
                "name": "echo",
                "arguments": json.dumps({"message": "saved"}),
                "status": "completed",
            }
            yield from parser.feed(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "status": "in_progress", "arguments": ""},
                }
            )
            yield from parser.feed(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc-1",
                    "output_index": 0,
                    "delta": item["arguments"],
                }
            )
            if completed_first_call and self.calls == 1:
                yield from parser.feed(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp-1", "status": "completed", "output": [item]},
                    }
                )
                yield parser.finish(LLMResponseDiagnostics(transport="responses"))
                return
            yield from parser.feed({"type": "response.unrecognized"})

    fake = ProtocolClient()
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([echo]))
    with pytest.raises(LLMProviderError, match="unsupported stream event"):
        list(conv.stream("use echo", run_id="protocol-failure"))
    assert fake.calls == (2 if completed_first_call else 1)
    assert echo.call_count == (1 if completed_first_call else 0)


def test_continue_after_tool_error_reuses_result_without_reinvoking_tool(tmp_path: Path) -> None:
    echo = EchoTool()

    @dataclass
    class RecoverableFollowupClient:
        calls: int = 0
        received_tools: list[list[dict[str, Any]] | None] = field(default_factory=list)
        received_messages: list[list[dict[str, Any]]] = field(default_factory=list)
        context_window: int = 128000

        def generate(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(text="")

        def stream(
            self,
            request: ModelRequest,
        ) -> Iterator[LLMStreamEvent]:
            self.calls += 1
            self.received_tools.append(request.tools)
            self.received_messages.append(request.messages)
            if self.calls == 1:
                yield LLMToolCallDelta(
                    index=0,
                    tool_call_id="call-recover",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "saved"}),
                )
                yield LLMTurnDone(stop_reason="tool_use")
                return
            if self.calls == 2:
                raise LLMNetworkError("follow-up timeout")
            if self.calls == 3:
                assert any(
                    m.get("role") == "tool" and "saved" in str(m.get("content"))
                    for m in request.messages
                )
                yield LLMToolCallDelta(
                    index=0,
                    tool_call_id="call-next",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "next"}),
                )
                yield LLMTurnDone(stop_reason="tool_use")
                return
            yield LLMTextDelta(text="已根据保存的结果整理完成。")
            yield LLMTurnDone(stop_reason="end_turn")

    fake = RecoverableFollowupClient()
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([echo]),
    )
    image = AttachmentRef(
        asset_id="c" * 64,
        display_name="evidence.png",
        kind="image",
        media_type="image/png",
        size_bytes=10,
        sha256="c" * 64,
    )

    with pytest.raises(LLMNetworkError):
        list(
            conv.stream(
                "please use the tool",
                attachments=[image],
                run_id="failed-run",
            )
        )
    conv.append_run_error_event(
        code="upstream_transient",
        message="模型服务连接不稳定",
        recoverable=True,
        source="test",
        run_id="failed-run",
    )

    events = list(conv.continue_after_tool_error(run_id="continue-run"))

    assert echo.call_count == 2  # saved action once, new action once
    assert fake.received_tools[-1]
    assert len([e for e in conv.session.events if e.type == "user_message"]) == 1
    assert [
        e.payload["tool_call_id"] for e in conv.session.events if e.type == "tool_call_request"
    ] == ["call-recover", "call-next"]
    assert any(
        message.get("role") == "system"
        and "<image_evidence_boundary>" in str(message.get("content", ""))
        for message in fake.received_messages[-1]
    )
    assert any(
        isinstance(event, TextDelta) and event.text == "已根据保存的结果整理完成。"
        for event in events
    )
    assert conv.session.events[-1].type == "assistant_message"
    assert conv.session.events[-1].meta["run_id"] == "continue-run"


def test_network_retry_after_two_tools_does_not_repeat_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第三次 LLM 初始化网络失败后重试，只重放 LLM 请求，不重复前两次工具。"""
    echo = EchoTool()
    responses: list[Any] = [
        iter([_tool_call_chunk("call_1", "first")]),
        iter([_tool_call_chunk("call_2", "second")]),
        LiteLLMTimeout("timed out", model="deepseek/test", llm_provider="deepseek"),
        iter([_text_chunk("两次搜索结果整理完成。")]),
    ]
    completion_calls = 0

    def fake_completion(**_kwargs: Any) -> Any:
        nonlocal completion_calls
        response = responses[completion_calls]
        completion_calls += 1
        if isinstance(response, Exception):
            raise response
        return response

    async def completion(**kwargs: Any) -> Any:
        chunks = fake_completion(**kwargs)

        async def iterate() -> Any:
            for chunk in chunks:
                yield chunk

        return iterate()

    monkeypatch.setattr("llm_providers.client.litellm.acompletion", completion)
    client = LLMClient(
        ProviderSpec(model="deepseek/test", api_key="test", context_window=128_000),
        network_retries=1,
        network_retry_backoff_seconds=0,
    )
    conv = _make_conversation(tmp_path, fake_llm=client, registry=ToolRegistry([echo]))

    events = list(conv.stream("run twice"))

    assert completion_calls == 4
    assert echo.call_count == 2
    assert [event.tool_call_id for event in events if isinstance(event, ToolCallRequest)] == [
        "call_1",
        "call_2",
    ]
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == (
        "两次搜索结果整理完成。"
    )
    assert isinstance(events[-1], TurnDone)


def test_desktop_disconnect_retracts_uncommitted_text_and_does_not_repeat_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.system_prompt import DESKTOP_CHAT_SURFACE

    from agent import AssistantMessageReset

    echo = EchoTool()
    client = LLMClient(
        ProviderSpec(model="deepseek/test", api_key="test", context_window=128_000),
        network_retry_backoff_seconds=0,
    )
    calls = 0

    def attempt(*args: Any, **kwargs: Any) -> Iterator[LLMStreamEvent]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield LLMToolCallDelta(
                index=0,
                tool_call_id="once",
                tool_name="echo",
                args_json_delta='{"message":"saved"}',
            )
            yield LLMTurnDone(stop_reason="tool_use")
        elif calls == 2:
            yield LLMTextDelta(text="uncommitted")
            yield LLMToolCallDelta(
                index=0, tool_call_id="broken", tool_name="echo", args_json_delta='{"message":'
            )
            raise LLMNetworkError("disconnect after text and partial tool")
        elif calls == 3:
            yield LLMToolCallDelta(
                index=0, tool_call_id="next", tool_name="echo", args_json_delta='{"message":"next"}'
            )
            yield LLMTurnDone(stop_reason="tool_use")
        else:
            yield LLMTextDelta(text="finished")
            yield LLMTurnDone(stop_reason="end_turn")

    monkeypatch.setattr(client, "_stream_once", attempt)
    conv = _make_conversation(tmp_path, fake_llm=client, registry=ToolRegistry([echo]))
    events = list(conv.stream("complete both actions", surface=DESKTOP_CHAT_SURFACE))
    assert calls == 4
    assert echo.call_count == 2
    assert [e.tool_call_id for e in events if isinstance(e, ToolCallRequest)] == ["once", "next"]
    partial = next(e for e in events if isinstance(e, TextDelta) and e.text == "uncommitted")
    resets = [e for e in events if isinstance(e, AssistantMessageReset)]
    assert len(resets) == 1 and partial.message_id in resets[0].message_ids
    assert [e.payload["content"] for e in conv.session.events if e.type == "assistant_message"] == [
        "finished"
    ]


def test_reused_provider_tool_call_id_gets_unique_event_ids(tmp_path: Path) -> None:
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="web_search_1",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "a"}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="web_search_1",
                    tool_name="echo",
                    args_json_delta=json.dumps({"message": "b"}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([echo]))

    events = list(conv.stream("run twice"))
    requests = [ev for ev in events if isinstance(ev, ToolCallRequest)]
    results = [ev for ev in events if isinstance(ev, ToolCallResult)]

    assert [ev.tool_call_id for ev in requests] == ["web_search_1", "web_search_1"]
    assert requests[0].event_tool_call_id != requests[1].event_tool_call_id
    assert [ev.event_tool_call_id for ev in results] == [
        requests[0].event_tool_call_id,
        requests[1].event_tool_call_id,
    ]


def test_recall_tool_receives_hidden_current_turn_context(tmp_path: Path) -> None:
    """``recall_past_chats`` 执行时拿到当前 session / turn 起点，落盘参数仍保持原样。"""
    recall = SpyRecallTool()
    registry = ToolRegistry([recall])
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="让我回忆一下。"),
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call_recall",
                    tool_name="recall_past_chats",
                    args_json_delta=json.dumps({"limit": 10}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMTextDelta(text="想起来了。"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)

    events = list(conv.stream("我们最近聊了啥？"))

    assert len(recall.seen_args) == 1
    invocation_args = recall.seen_args[0]
    assert invocation_args["limit"] == 10
    assert invocation_args["__agent_friend_current_session_id"] == conv.session.session_id
    assert invocation_args["__agent_friend_current_turn_start_index"] == 1
    assert invocation_args["__agent_friend_current_persona_id"] == conv.session.current_persona_id
    assert "__agent_friend_original_user_input" not in invocation_args
    assert "__agent_friend_runtime_now" in invocation_args

    tool_reqs = [ev for ev in events if isinstance(ev, ToolCallRequest)]
    assert len(tool_reqs) == 1
    assert tool_reqs[0].args == {"limit": 10}

    request_event = next(ev for ev in conv.session.events if ev.type == "tool_call_request")
    assert request_event.payload["args"] == {"limit": 10}


def test_recovery_tool_receives_hidden_current_session_without_persisting_it(
    tmp_path: Path,
) -> None:
    class SpyRecoveryTool:
        name: ClassVar[str] = "recover_tool_result"
        description: ClassVar[str] = "recover a prior result"
        parameters_schema: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"result_ref": {"type": "string"}},
            "required": ["result_ref"],
        }

        def __init__(self) -> None:
            self.seen_args: list[dict[str, Any]] = []

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            self.seen_args.append(dict(args))
            return ToolResult("recovered")

    recovery = SpyRecoveryTool()
    public_args = {"result_ref": "result-1"}
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call-recover",
                    tool_name="recover_tool_result",
                    args_json_delta=json.dumps(public_args),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="找到了。"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([recovery]),
    )

    list(conv.stream("继续之前的结果"))

    assert recovery.seen_args == [
        {**public_args, "__agent_friend_current_session_id": conv.session.session_id}
    ]
    request_event = next(ev for ev in conv.session.events if ev.type == "tool_call_request")
    assert request_event.payload["args"] == public_args


def test_call_history_uses_host_identity_and_exposes_only_public_schema(tmp_path: Path) -> None:
    from agent.tools.builtin.tool_call_history import InspectToolCallsTool

    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    tool = InspectToolCallsTool(store)
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="discover",
                    tool_name=tool.name,
                    args_json_delta='{"mode":"search"}',
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="没有历史调用。"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([tool]))
    list(conv.stream("找一下原调用"))
    result = next(e for e in conv.session.events if e.type == "tool_call_result")
    assert not result.payload["is_error"]
    assert json.loads(result.payload["content"])["results"] == []
    specs = fake.received_tools[0]
    assert specs is not None
    assert specs[0]["function"]["parameters"] == tool.parameters_schema
    assert "__agent_friend_current_session_id" not in json.dumps(specs)
    request = next(e for e in conv.session.events if e.type == "tool_call_request")
    assert request.payload["args"] == {"mode": "search"}


def test_mid_tool_compaction_failure_keeps_completed_call_without_replay(tmp_path: Path) -> None:
    from agent.context import ContextCompactionUnavailableError, SummarizingContextManager

    from llm_providers import LLMTransientProviderError

    class FailingSummaryClient(_ScriptedLLMClient):
        def generate(self, request: ModelRequest) -> ModelResponse:
            raise LLMTransientProviderError("summary unavailable")

    class LargeResultTool:
        name: ClassVar[str] = "large_result"
        description: ClassVar[str] = "Produce a deterministic result"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        calls = 0

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            self.calls += 1
            fake.context_window = 14_000
            return ToolResult("completed-operation " + "x" * 40_000)

    tool = LargeResultTool()
    fake = FailingSummaryClient(
        context_window=30_000,
        script=[
            [
                LLMTextDelta(text="previous task result" + "x" * 10_000),
                LLMTurnDone(stop_reason="end_turn"),
            ],
            [
                LLMToolCallDelta(
                    index=0, tool_call_id="once", tool_name=tool.name, args_json_delta="{}"
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMTextDelta(text="continued from completed result"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ],
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([tool]))
    conv._context_manager = SummarizingContextManager()
    assert conv.send("previous task").startswith("previous task result")
    with pytest.raises(ContextCompactionUnavailableError):
        conv.send("perform operation")
    assert tool.calls == 1 and fake.turn_idx == 2
    results = [e for e in conv.session.events if e.type == "tool_call_result"]
    assert len(results) == 1 and "completed-operation" in results[0].payload["content"]
    fake.context_window = 300_000
    assert conv.send("continue") == "continued from completed result"
    assert tool.calls == 1


def test_recoverable_result_is_pinned_unless_recovery_tool_is_registered(
    tmp_path: Path,
) -> None:
    class ReadTool:
        name: ClassVar[str] = "read_test"
        description: ClassVar[str] = "read"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult("large read result")

    class RecoverySurface:
        name: ClassVar[str] = "recover_tool_result"
        description: ClassVar[str] = "recover"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult("unused")

    script: list[list[LLMStreamEvent]] = [
        [
            LLMToolCallDelta(0, "call-read", "read_test", "{}"),
            LLMTurnDone(stop_reason="tool_use"),
        ],
        [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
    ]
    without_recovery = _make_conversation(
        tmp_path,
        fake_llm=_ScriptedLLMClient(script=script),
        registry=ToolRegistry([ReadTool()]),
    )
    list(without_recovery.stream("read"))
    pinned = next(
        event for event in without_recovery.session.events if event.type == "tool_call_result"
    )
    assert pinned.meta["extra"]["context_retention"] == "pinned"

    with_recovery = _make_conversation(
        tmp_path,
        fake_llm=_ScriptedLLMClient(script=script),
        registry=ToolRegistry([ReadTool(), RecoverySurface()]),
    )
    list(with_recovery.stream("read"))
    recoverable = next(
        event for event in with_recovery.session.events if event.type == "tool_call_result"
    )
    assert recoverable.meta["extra"]["context_retention"] == "recoverable"


def test_social_tool_receives_hidden_session_without_persisting_it(tmp_path: Path) -> None:
    social = SpySocialSearchTool()
    registry = ToolRegistry([social])
    public_args = {"platform": "xiaohongshu", "query": "关键词"}
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call-social",
                    tool_name="social_search",
                    args_json_delta=json.dumps(public_args),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="没有找到。"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)

    list(conv.stream("去小红书搜一下"))

    assert social.seen_args == [
        {**public_args, "__agent_friend_current_session_id": conv.session.session_id}
    ]
    request_event = next(ev for ev in conv.session.events if ev.type == "tool_call_request")
    assert request_event.payload["args"] == public_args


def test_bilibili_opus_direct_url_uses_current_turn_authorization(tmp_path: Path) -> None:
    connector = _DirectBilibiliConnector()
    service = SocialPlatformService((connector,))
    public_url = connector.canonical_url
    provided_url = f"{public_url}?spm_id_from=333.1369.0.0"
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call-opus",
                    tool_name="social_read",
                    args_json_delta=json.dumps({"url": public_url}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="已根据 Opus 正文完成回答。"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([SocialReadTool(service)]),
    )

    events = list(conv.stream(f"{provided_url}，来学习其中一只鬼的知识吧"))

    assert connector.read_calls == 1
    request = next(event for event in events if isinstance(event, ToolCallRequest))
    assert request.tool_name == "social_read"
    assert request.args == {"url": public_url}
    result = next(event for event in events if isinstance(event, ToolCallResult))
    assert not result.is_error
    payload = json.loads(result.text)
    assert payload["status"] == "ok"
    assert payload["url"] == public_url
    persisted_request = next(
        event for event in conv.session.events if event.type == "tool_call_request"
    )
    assert persisted_request.payload["args"] == {"url": public_url}
    assert all(
        event.tool_name not in {"web_fetch", "social_search"}
        for event in events
        if isinstance(event, ToolCallRequest)
    )
    service.close()


def test_social_auth_request_receives_frozen_im_origin_and_continuation_replans(
    tmp_path: Path,
) -> None:
    auth = SpySocialAuthRequestTool()
    social = SpySocialSearchTool()
    public_auth_args = {
        "platform": "xiaohongshu",
        "missing_capabilities": ["note_body"],
        "reason": "需要笔记正文才能回答",
    }
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call-auth",
                    tool_name="request_social_auth",
                    args_json_delta=json.dumps(public_auth_args, ensure_ascii=False),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="请到电脑端完成登录。"), LLMTurnDone(stop_reason="end_turn")],
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call-search-after-auth",
                    tool_name="social_search",
                    args_json_delta=json.dumps(
                        {"platform": "xiaohongshu", "query": "关键词"},
                        ensure_ascii=False,
                    ),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="登录后已完成原目标。"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([auth, social]),
    )
    destination = "qq|bind|c2c|chat|im:qq:chat"

    list(
        conv.stream(
            "读取这篇小红书正文",
            surface=IM_QQ_SURFACE,
            interaction_destination_id=destination,
            run_id="initial-run",
        )
    )
    continuation_events = list(
        conv.continue_after_external_action(
            source_kind="social_auth_completed",
            system_prompt_addendum="本地登录已完成，请重新规划。",
            presentation="none",
            surface=IM_QQ_SURFACE,
            interaction_destination_id=destination,
            run_id="continuation-run",
        )
    )

    assert auth.seen_args == [
        {
            **public_auth_args,
            "__agent_friend_current_session_id": conv.session.session_id,
            "__agent_friend_interaction_surface": "im:qq",
            "__agent_friend_interaction_destination_id": destination,
        }
    ]
    auth_request_event = next(
        event
        for event in conv.session.events
        if event.type == "tool_call_request" and event.payload["tool_name"] == "request_social_auth"
    )
    assert auth_request_event.payload["args"] == public_auth_args
    assert social.seen_args == [
        {
            "platform": "xiaohongshu",
            "query": "关键词",
            "__agent_friend_current_session_id": conv.session.session_id,
        }
    ]
    assert [event.type for event in conv.session.events].count("user_message") == 1
    system_trigger = next(
        event
        for event in conv.session.events
        if event.type == "system_trigger"
        and event.payload["source_kind"] == "social_auth_completed"
    )
    assert system_trigger.payload["presentation"] == "none"
    assert system_trigger.meta["run_id"] == "continuation-run"
    assert any(
        isinstance(event, TextDelta) and event.text == "登录后已完成原目标。"
        for event in continuation_events
    )
    assert any(
        message.get("content") == "本地登录已完成，请重新规划。" for message in fake.received[2]
    )


def test_model_routes_history_query_with_semantic_structured_arguments(tmp_path: Path) -> None:
    recall = SpyRecallTool()
    structured_args = {
        "session_title": "诗欣im",
        "since": "今天",
        "until": "今天",
        "mode": "summary",
    }
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call_recall",
                    tool_name="recall_past_chats",
                    args_json_delta=json.dumps(structured_args, ensure_ascii=False),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="今天没有消息。"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([recall]))

    events = list(conv.stream("去看看诗欣im这个会话里今天有没发什么"))

    requests = [event for event in events if isinstance(event, ToolCallRequest)]
    results = [event for event in events if isinstance(event, ToolCallResult)]
    assert len(requests) == 1
    assert len(results) == 1
    assert requests[0].tool_name == "recall_past_chats"
    assert requests[0].origin == "model"
    assert results[0].origin == "model"
    assert requests[0].args == structured_args
    assert "search_text" not in requests[0].args
    assert "__agent_friend_original_user_input" not in recall.seen_args[0]
    request_event = next(
        event for event in conv.session.events if event.type == "tool_call_request"
    )
    assert request_event.meta["tool_call_origin"] == "model"
    assert any(message.get("role") == "tool" for message in fake.received[1])


def test_closing_stream_after_turndone_does_not_write_partial_duplicate(tmp_path: Path) -> None:
    """消费方拿到 TurnDone 后关闭 generator，不应误落一条 partial=True 重复回复。"""
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="完整回复"),
                LLMTurnDone(stop_reason="end_turn"),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([]))

    stream = cast(Generator[Any, None, None], conv.stream("hi"))
    first = next(event for event in stream if isinstance(event, TextDelta))
    done = next(event for event in stream if isinstance(event, TurnDone))
    assert isinstance(first, TextDelta)
    assert isinstance(done, TurnDone)

    stream.close()

    assistant_events = [ev for ev in conv.session.events if ev.type == "assistant_message"]
    assert len(assistant_events) == 1
    assert assistant_events[0].payload["content"] == "完整回复"
    assert assistant_events[0].payload["partial"] is False


# ===== 失败兜底 =====


def test_tool_failure_does_not_break_loop(tmp_path: Path) -> None:
    """工具抛异常 → 转 ToolResult(is_error=True) → 循环不中断 → 第二轮整合输出后结束。"""
    fail = FailingTool()
    registry = ToolRegistry([fail])
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    index=0,
                    tool_call_id="call_x",
                    tool_name="fail_tool",
                    args_json_delta="{}",
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMTextDelta(text="抱歉，刚才碰到点问题。"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)

    events = list(conv.stream("call fail"))
    tool_results = [ev for ev in events if isinstance(ev, ToolCallResult)]
    dones = [ev for ev in events if isinstance(ev, TurnDone)]

    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    assert "simulated tool crash" in tool_results[0].text

    text = "".join(ev.text for ev in events if isinstance(ev, TextDelta))
    assert "抱歉" in text

    assert len(dones) == 1
    assert dones[0].stop_reason == "end_turn"


# ===== 触上限 =====


def test_explicit_continuation_budget_triggers_finalization(tmp_path: Path) -> None:
    """显式 continuation 预算触发后禁用工具并收尾。"""
    echo = EchoTool()
    registry = ToolRegistry([echo])

    def tool_use_events(call_id: str) -> list[LLMStreamEvent]:
        return [
            LLMToolCallDelta(
                index=0,
                tool_call_id=call_id,
                tool_name="echo",
                args_json_delta=json.dumps({"message": "loop"}),
            ),
            LLMTurnDone(stop_reason="tool_use"),
        ]

    fake = _ScriptedLLMClient(
        script=[tool_use_events(f"call_{i}") for i in range(6)]
        + [
            [
                LLMTextDelta(text="discarded-finalization-draft"),
                LLMRetryScheduled(attempt=2, max_attempts=6, delay_seconds=0),
                LLMTextDelta(text="我尽力了，没找到更好的回答。"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=registry,
        tool_run_policy=ToolRunPolicy(max_model_continuations=5),
    )

    events = list(conv.stream("loop forever"))
    dones = [ev for ev in events if isinstance(ev, TurnDone)]

    assert len(dones) == 1
    assert dones[0].stop_reason == "budget_exhausted"
    assert dones[0].budget_dimension == "model_continuations"
    assert dones[0].total_tool_calls == 6

    text = "".join(ev.text for ev in events if isinstance(ev, TextDelta))
    assert "尽力了" in text

    assert echo.call_count == 6
    assert "discarded-finalization-draft" not in str(conv.session.events[-1].payload)
    assert not any(
        "discarded-finalization-draft" in str(event.payload.get("content", ""))
        for event in conv.session.events
        if event.type == "assistant_message"
    )

    # 兜底调用使用 trusted host user packet，避免改写稳定 system envelope。
    finalization_messages = fake.received[-1]
    host_messages = [m for m in finalization_messages if m.get("role") == "user"]
    assert any("不要再调用任何工具" in str(m.get("content") or "") for m in host_messages)


def test_system_text_consumer_discards_retried_draft(tmp_path: Path) -> None:
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMTextDelta(text="discarded-system-draft"),
                LLMRetryScheduled(attempt=2, max_attempts=6, delay_seconds=0),
                LLMTextDelta(text="completed"),
                LLMTurnDone(stop_reason="end_turn"),
            ]
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake)
    list(
        conv.dispatch_system_turn(
            source_kind="cron:test",
            system_prompt_addendum="notify",
            output_visibility="user",
        )
    )
    assert [e.payload["content"] for e in conv.session.events if e.type == "assistant_message"] == [
        "completed"
    ]


def test_default_policy_can_complete_after_more_than_five_continuations(tmp_path: Path) -> None:
    echo = EchoTool()
    registry = ToolRegistry([echo])

    def tool_use_events(call_id: str) -> list[LLMStreamEvent]:
        return [
            LLMToolCallDelta(
                index=0,
                tool_call_id=call_id,
                tool_name="echo",
                args_json_delta=json.dumps({"message": call_id}),
            ),
            LLMTurnDone(stop_reason="tool_use"),
        ]

    fake = _ScriptedLLMClient(
        script=[tool_use_events(f"call_{i}") for i in range(7)]
        + [[LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)
    events = list(conv.stream("long but useful"))
    done = next(event for event in events if isinstance(event, TurnDone))
    assert done.stop_reason == "end_turn"
    assert done.total_tool_calls == 7
    assert done.model_continuations == 7
    final_assistant = [event for event in conv.session.events if event.type == "assistant_message"][
        -1
    ]
    assert final_assistant.meta["tool_run"]["total_tool_calls"] == 7


def test_web_search_result_authorizes_web_fetch_only_within_same_turn(tmp_path: Path) -> None:
    target = "https://example.com/source"
    evidence = make_public_evidence(
        target,
        source="web_search_result",
        source_call_id="search-1",
    )

    class SearchTool:
        name: ClassVar[str] = "web_search"
        description: ClassVar[str] = "test search"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult(
                json.dumps({"results": [{"evidence_ref": evidence.evidence_ref, "url": target}]}),
                meta={
                    "source_urls": [target],
                    **evidence_result_meta([evidence]),
                },
            )

    class DnsFailingLocalReader:
        name: ClassVar[str] = "local"

        def __init__(self) -> None:
            self.calls = 0

        def open(self, url: str, *, focus: str | None = None) -> ExtractedContent:
            self.calls += 1
            raise UntrustedDnsResolutionError("mixed DNS answers")

    class FetchProvider:
        name: ClassVar[str] = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent:
            self.calls += 1
            return ExtractedContent(url, url, "verified source")

    provider = FetchProvider()
    local_reader = DnsFailingLocalReader()
    registry = ToolRegistry(
        [
            SearchTool(),
            WebFetchTool(provider, html_reader=local_reader),  # type: ignore[arg-type]
        ]
    )
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "search-1", "web_search", '{"query":"x"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(
                    0,
                    "fetch-1",
                    "web_fetch",
                    json.dumps({"evidence_ref": evidence.evidence_ref}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="verified"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)
    events = list(conv.stream("check"))
    results = [event for event in events if isinstance(event, ToolCallResult)]
    assert provider.calls == 1
    assert local_reader.calls == 1
    assert [event.is_error for event in results] == [False, False]
    search_event = next(
        event
        for event in conv.session.events
        if event.type == "tool_call_result" and event.payload["tool_name"] == "web_search"
    )
    assert "source_urls" not in search_event.meta.get("extra", {})


def test_public_evidence_survives_conversation_restart_for_next_open(tmp_path: Path) -> None:
    target = "https://example.com/persisted-source"
    evidence = make_public_evidence(target, source="web_search_result", label="source")

    class SearchTool:
        name: ClassVar[str] = "web_search"
        description: ClassVar[str] = "test search"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult(
                json.dumps({"results": [{"evidence_ref": evidence.evidence_ref, "url": target}]}),
                meta={
                    "evidence_keys": [target],
                    **evidence_result_meta([evidence]),
                },
            )

    class FetchProvider:
        name: ClassVar[str] = "fake"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent:
            self.calls.append(url)
            return ExtractedContent(url, url, "persisted evidence opened")

    provider = FetchProvider()
    first_llm = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "search-1", "web_search", '{"query":"x"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="found"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    registry = ToolRegistry([SearchTool(), WebFetchTool(provider)])
    first = _make_conversation(tmp_path, fake_llm=first_llm, registry=registry)
    list(first.stream("find source"))

    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    loaded = store.load(first.session.session_id)
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    builder = MarkdownPromptBuilder(persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog)
    second_llm = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    0,
                    "fetch-1",
                    "web_fetch",
                    json.dumps({"evidence_ref": evidence.evidence_ref}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="opened"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    restarted = Conversation(
        session=loaded,
        store=store,
        llm_client=second_llm,
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
            persona_id=persona_id, catalog=catalog
        ),
        catalog=catalog,
        tool_registry=registry,
    )
    results = [
        event
        for event in restarted.stream("open the saved source")
        if isinstance(event, ToolCallResult)
    ]

    assert provider.calls == [target]
    assert results[0].is_error is False


def test_edit_resend_does_not_restore_evidence_from_rewritten_branch(tmp_path: Path) -> None:
    target = "https://example.com/inactive-branch"
    evidence = make_public_evidence(target, source="web_search_result")

    class SearchTool:
        name: ClassVar[str] = "web_search"
        description: ClassVar[str] = "test search"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult(
                "found",
                meta={
                    "evidence_keys": [target],
                    **evidence_result_meta([evidence]),
                },
            )

    class FetchProvider:
        name: ClassVar[str] = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent:
            self.calls += 1
            return ExtractedContent(url, url, "should not open")

    provider = FetchProvider()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "search-1", "web_search", '{"query":"old"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="old answer"), LLMTurnDone(stop_reason="end_turn")],
            [
                LLMToolCallDelta(
                    0,
                    "fetch-1",
                    "web_fetch",
                    json.dumps({"evidence_ref": evidence.evidence_ref}),
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="new answer"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([SearchTool(), WebFetchTool(provider)]),
    )
    list(conv.stream("old question"))
    events = list(conv.edit_resend_latest("new question", expected_user_content="old question"))
    result = next(event for event in events if isinstance(event, ToolCallResult))

    assert result.is_error is True
    assert provider.calls == 0


def test_web_fetch_accepts_url_from_no_current_turn_source(tmp_path: Path) -> None:
    class FetchProvider:
        name: ClassVar[str] = "fake"

        def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent:
            return ExtractedContent(url, url, "direct page body")

    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(
                    0,
                    "fetch-1",
                    "web_fetch",
                    '{"url":"https://example.com/not-provided"}',
                ),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="read page"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    registry = ToolRegistry([WebFetchTool(FetchProvider())])
    events = list(_make_conversation(tmp_path, fake_llm=fake, registry=registry).stream("check"))
    result = next(event for event in events if isinstance(event, ToolCallResult))
    assert not result.is_error
    assert "direct page body" in result.text
    definitions = fake.received_tools[0]
    assert definitions is not None
    definition = next(
        item["function"] for item in definitions if item["function"]["name"] == "web_fetch"
    )
    assert "无需先搜索或登记来源" in definition["description"]
    assert "不要构造新 URL" not in definition["description"]
    assert set(definition["parameters"]["properties"]) == {"url", "evidence_ref", "focus"}


def test_each_time_sensitive_tool_reads_fresh_action_time_without_prompt_clock(
    tmp_path: Path,
) -> None:
    seen_args: list[dict[str, Any]] = []

    class CaptureTodoTool:
        name: ClassVar[str] = "todo_manage"
        description: ClassVar[str] = "capture runtime"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            seen_args.append(args)
            return ToolResult("ok")

    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "todo-1", "todo_manage", '{"operation":"create"}'),
                LLMToolCallDelta(1, "todo-2", "todo_manage", '{"operation":"create"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    initial = datetime(2026, 7, 28, 22, 4, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    first_inference = datetime(2026, 7, 28, 22, 4, 13, tzinfo=ZoneInfo("Asia/Shanghai"))
    continuation = datetime(2026, 7, 28, 22, 6, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    moments = iter([initial, first_inference, continuation])
    trusted = ToolRunContext.create(
        "今天星期几",
        clock=lambda: next(moments),
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([CaptureTodoTool()]),
        tool_run_context_factory=lambda _: trusted,
        time_provider=SimpleNamespace(now=lambda: initial),
    )
    list(conv.stream("今天星期几"))
    assert len(seen_args) == 2
    assert {args["__agent_friend_runtime_now"] for args in seen_args} == {
        first_inference.isoformat(),
        continuation.isoformat(),
    }
    assert {args["__agent_friend_runtime_timezone"] for args in seen_args} == {"Asia/Shanghai"}
    assert all(
        first_inference.isoformat(timespec="seconds") not in str(message.get("content") or "")
        and continuation.isoformat(timespec="seconds") not in str(message.get("content") or "")
        for request in fake.received
        for message in request
    )
    assert (
        sum(
            'contributor="calendar"' in str(message.get("content") or "")
            for message in fake.received[1]
        )
        == 1
    )
    assert fake.received[1][: len(fake.received[0])] == fake.received[0]
    first_appended_role = fake.received[1][len(fake.received[0])]["role"]
    assert first_appended_role == "assistant"
    tool_positions = [
        index for index, message in enumerate(fake.received[1]) if message.get("role") == "tool"
    ]
    assert tool_positions
    assert all(index > len(fake.received[0]) for index in tool_positions)
    request = next(event for event in conv.session.events if event.type == "tool_call_request")
    assert "__agent_friend_runtime_now" not in request.payload["args"]
    assert all("<runtime_now" not in message.content for message in conv.session.messages)
    assert all(event.type != "model_context" for event in conv.session.events)
    assert all("<runtime_now" not in message.content for message in conv.session.llm_messages)


def test_repeated_turns_keep_one_calendar_snapshot_without_persistence(
    tmp_path: Path,
) -> None:
    trusted = ToolRunContext.create(
        "same clock",
        now=datetime(2026, 7, 28, 22, 4, 13, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    fake = _ScriptedLLMClient(
        script=[
            [LLMTextDelta(text="one"), LLMTurnDone(stop_reason="end_turn")],
            [LLMTextDelta(text="two"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        tool_run_context_factory=lambda _: trusted,
        time_provider=SimpleNamespace(now=lambda: trusted.trusted_now),
    )

    list(conv.stream("first"))
    list(conv.stream("second"))

    assert all(event.type != "model_context" for event in conv.session.events)
    assert fake.received[1][: len(fake.received[0])] == fake.received[0]
    assert (
        sum(
            'contributor="calendar"' in str(message.get("content") or "")
            for message in fake.received[1]
        )
        == 1
    )
    assert all(
        trusted.trusted_now.isoformat(timespec="seconds") not in str(message.get("content") or "")
        for message in fake.received[1]
    )


def test_current_calendar_delta_follows_conflicting_long_history(tmp_path: Path) -> None:
    """旧回答中的 31 号不能覆盖当前用户前的新日历 delta。"""
    old_context = ToolRunContext.create(
        "旧问题",
        now=datetime(2026, 7, 27, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    current_context = ToolRunContext.create(
        "今天发工资",
        now=datetime(2026, 7, 28, 22, 4, 13, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    contexts = iter([old_context, current_context])
    host_time = {"value": old_context.trusted_now}
    fake = _ScriptedLLMClient(
        script=[
            [LLMTextDelta(text="自然月到 31 号。"), LLMTurnDone(stop_reason="end_turn")],
            [LLMTextDelta(text="收到。"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        tool_run_context_factory=lambda _: next(contexts),
        time_provider=SimpleNamespace(now=lambda: host_time["value"]),
    )

    list(conv.stream("旧问题"))
    host_time["value"] = current_context.trusted_now
    list(conv.stream("我是 20 号入职，今天发工资"))

    messages = fake.received[1]
    old_answer_index = next(
        index for index, message in enumerate(messages) if "31 号" in message.get("content", "")
    )
    calendar_index = next(
        index
        for index, message in enumerate(messages)
        if 'contributor="calendar" kind="delta"' in message.get("content", "")
    )
    current_user_index = next(
        index
        for index, message in enumerate(messages)
        if message.get("content") == "我是 20 号入职，今天发工资"
    )
    assert old_answer_index < calendar_index < current_user_index
    assert 'date="2026-07-28" timezone="Asia/Shanghai"' in messages[calendar_index]["content"]
    assert any(
        'date="2026-07-27" timezone="Asia/Shanghai"' in message.get("content", "")
        for message in messages
    )


def test_repeated_identical_failures_only_add_advisory_and_continue(tmp_path: Path) -> None:
    fail = FailingTool()
    registry = ToolRegistry([fail])
    repeated: list[list[LLMStreamEvent]] = [
        [
            LLMToolCallDelta(0, f"fail-{index}", "fail_tool", "{}"),
            LLMTurnDone(stop_reason="tool_use"),
        ]
        for index in range(3)
    ]
    fake = _ScriptedLLMClient(
        script=[*repeated, [LLMTextDelta(text="stopped"), LLMTurnDone(stop_reason="end_turn")]]
    )
    events = list(_make_conversation(tmp_path, fake_llm=fake, registry=registry).stream("retry"))
    done = next(event for event in events if isinstance(event, TurnDone))
    assert done.stop_reason == "end_turn"
    assert done.total_tool_calls == 3
    assert any(
        message.get("role") == "user" and "这只是进展提示" in str(message.get("content") or "")
        for message in fake.received[3]
    )


def test_unavailable_filesystem_approval_only_advises_after_changed_requests(
    tmp_path: Path,
) -> None:
    tool = RequestFilesystemAccessTool(
        None,
        ProtectedPathPolicy(
            product_roots=((tmp_path / "product").resolve(),),
            personal_skill_roots=((tmp_path / "personal-skills").resolve(),),
        ),
    )
    requests = [
        {
            "reason": "读取用户指定的说明文件",
            "items": [
                {
                    "item_id": "docs",
                    "locator": {"kind": "path", "path": str(tmp_path)},
                    "root_kind": "directory",
                    "permissions": ["search_metadata", "read_text"],
                }
            ],
        },
        {
            "reason": "核对本地安装包版本",
            "items": [
                {
                    "item_id": "docs",
                    "locator": {"kind": "path", "path": str(tmp_path)},
                    "root_kind": "directory",
                    "permissions": ["search_metadata", "read_text"],
                }
            ],
        },
        {
            "reason": "核对本地安装包版本",
            "items": [
                {
                    "item_id": "packages",
                    "locator": {"kind": "path", "path": str(tmp_path / "packages")},
                    "root_kind": "directory",
                    "permissions": ["search_metadata", "read_text"],
                }
            ],
        },
    ]
    repeated: list[list[LLMStreamEvent]] = [
        [
            LLMToolCallDelta(
                0,
                f"approval-{index}",
                tool.name,
                json.dumps(args, ensure_ascii=False),
            ),
            LLMTurnDone(stop_reason="tool_use"),
        ]
        for index, args in enumerate(requests)
    ]
    fake = _ScriptedLLMClient(
        script=[*repeated, [LLMTextDelta(text="请在桌面端继续。"), LLMTurnDone("end_turn")]]
    )

    events = list(
        _make_conversation(
            tmp_path,
            fake_llm=fake,
            registry=ToolRegistry([tool]),
        ).stream("检查本地安装包")
    )

    results = [event for event in events if isinstance(event, ToolCallResult)]
    done = next(event for event in events if isinstance(event, TurnDone))
    assert len(results) == 3
    assert all(event.is_error for event in results)
    assert done.stop_reason == "end_turn"
    assert done.total_tool_calls == 3
    assert any(
        message.get("role") == "user" and "这只是进展提示" in str(message.get("content") or "")
        for message in fake.received[3]
    )


def test_stale_filesystem_scope_only_advises_after_changed_reads(tmp_path: Path) -> None:
    policy = ProtectedPathPolicy(
        product_roots=((tmp_path / "product").resolve(),),
        personal_skill_roots=((tmp_path / "personal-skills").resolve(),),
    )
    tool = FilesystemOperationTool("filesystem_read_text", FilesystemService(policy))
    attempts = [
        {"filesystem_scope_id": "scope-from-previous-turn"},
        {"filesystem_scope_id": "scope-from-previous-turn", "max_lines": 50},
        {
            "filesystem_scope_id": "scope-from-previous-turn",
            "max_lines": 30,
            "encoding": "utf-8",
        },
    ]
    repeated: list[list[LLMStreamEvent]] = [
        [
            LLMToolCallDelta(
                0,
                f"stale-scope-{index}",
                tool.name,
                json.dumps(args),
            ),
            LLMTurnDone(stop_reason="tool_use"),
        ]
        for index, args in enumerate(attempts)
    ]
    fake = _ScriptedLLMClient(
        script=[*repeated, [LLMTextDelta(text="需要重新授权。"), LLMTurnDone("end_turn")]]
    )

    events = list(
        _make_conversation(
            tmp_path,
            fake_llm=fake,
            registry=ToolRegistry([cast(Tool, tool)]),
        ).stream("继续读取文件")
    )

    results = [event for event in events if isinstance(event, ToolCallResult)]
    done = next(event for event in events if isinstance(event, TurnDone))
    assert len(results) == 3
    assert all(
        json.loads(event.text)["permission_reason"] == "scope_unavailable" for event in results
    )
    assert done.stop_reason == "end_turn"
    assert done.total_tool_calls == 3


def test_user_action_hint_does_not_stop_model_from_trying_another_tool(tmp_path: Path) -> None:
    tool = UserActionRequiredTool()
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "setup", tool.name, "{}"),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(0, "fallback", "echo", '{"message":"fallback"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="请先完成初始化。"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    events = list(
        _make_conversation(
            tmp_path,
            fake_llm=fake,
            registry=ToolRegistry([tool, echo]),
        ).stream("继续")
    )
    done = next(event for event in events if isinstance(event, TurnDone))
    assert done.stop_reason == "end_turn"
    assert done.total_tool_calls == 2
    assert echo.call_count == 1
    assert len(fake.received) == 3


def test_soft_budget_reminder_is_injected_once(tmp_path: Path) -> None:
    echo = EchoTool()
    calls: list[list[LLMStreamEvent]] = [
        [
            LLMToolCallDelta(
                0,
                f"echo-{index}",
                "echo",
                json.dumps({"message": str(index)}),
            ),
            LLMTurnDone(stop_reason="tool_use"),
        ]
        for index in range(2)
    ]
    fake = _ScriptedLLMClient(
        script=[*calls, [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")]]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([echo]),
        tool_run_policy=ToolRunPolicy(max_model_continuations=3, soft_limit_ratio=0.5),
    )
    list(conv.stream("work"))
    reminders = [
        message["content"]
        for request in fake.received
        for message in request
        if message.get("role") == "user"
        and "工具运行预算即将到达边界" in str(message.get("content") or "")
    ]
    assert len(reminders) == 1


def test_call_budget_executes_only_original_order_prefix_and_closes_all_calls(
    tmp_path: Path,
) -> None:
    echo = EchoTool()
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "one", "echo", '{"message":"one"}'),
                LLMToolCallDelta(1, "two", "echo", '{"message":"two"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="partial evidence"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(
        tmp_path,
        fake_llm=fake,
        registry=ToolRegistry([echo]),
        tool_run_policy=ToolRunPolicy(max_total_tool_calls=1),
    )
    events = list(conv.stream("two calls"))
    requests = [event for event in events if isinstance(event, ToolCallRequest)]
    results = [event for event in events if isinstance(event, ToolCallResult)]
    done = next(event for event in events if isinstance(event, TurnDone))
    assert echo.call_count == 1
    assert len(requests) == len(results) == 2
    assert results[1].is_error and "预算" in results[1].text
    assert done.stop_reason == "budget_exhausted"
    assert done.budget_dimension == "total_tool_calls"

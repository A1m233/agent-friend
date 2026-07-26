"""005 集成测：工具调用循环 happy path / 失败兜底 / 触上限。

覆盖 docs/requirements/005-engine-tool-calling-and-web-search/design.md §2.1
（M5.1 端到端单测覆盖）的三大场景：

- :func:`test_tool_call_happy_path` — LLM 触发 → 执行 → 喂回 → AI 整合回复
- :func:`test_tool_failure_does_not_break_loop` — tool 抛异常时循环不中断
- :func:`test_tool_loop_max_turns_triggers_finalization` — LLM 死循环达上限触发兜底
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from agent.tool_runtime import ToolRunPolicy
from agent.tools.builtin.nearby_place_search import (
    AnchorCandidate,
    NearbyPlace,
    NearbyPlaceSearchTool,
    PlaceSort,
)
from agent.tools.builtin.web_fetch import ExtractedContent, WebFetchTool
from litellm.exceptions import Timeout as LiteLLMTimeout

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    ModelResponseStatus,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    ToolRegistry,
    ToolResult,
    TurnDone,
)
from llm_providers import (
    LLMClient,
    LLMNetworkError,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ProviderSpec,
)

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
    context_window: int = 128000  # 009：Conversation 经此推导预算阈值

    def complete(
        self, messages: list[dict[str, Any]], **overrides: Any
    ) -> str:  # pragma: no cover - send 路径不测
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(messages)
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


def _make_conversation(
    tmp_path: Path,
    *,
    fake_llm: LLMClient | _ScriptedLLMClient,
    registry: ToolRegistry | None = None,
    llm_client_factory: Any | None = None,
    tool_run_policy: ToolRunPolicy | None = None,
) -> Conversation:
    """组装最小可跑的 :class:`Conversation`：fake LLM + 真 catalog + 内存内 store。"""
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    persona_id = BUILTIN_DEFAULT_PERSONA_ID
    store = JsonlSessionStore(base_dir=tmp_path / "sessions")
    session = Session.new(
        title="t",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=persona_id,
    )
    store.create(session)
    builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    def builder_factory(pid: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=pid, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=cast(LLMClient, fake_llm),
        llm_client_factory=llm_client_factory,
        context_manager=NaiveContextManager(),
        prompt_builder=builder,
        prompt_builder_factory=builder_factory,
        catalog=catalog,
        tool_registry=registry,
        tool_run_policy=tool_run_policy,
    )


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
        llm_client_factory=lambda _model: cast(LLMClient, new_client),
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

    def unavailable_factory(_model: str) -> LLMClient:
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

    # 事件落盘：session_meta + user + assistant(turn 0) + tool_call_request +
    # tool_call_result + assistant(turn 1)
    types = [ev.type for ev in conv.session.events]
    assert types[0] == "session_meta"
    assert types.count("user_message") == 1
    assert types.count("assistant_message") == 2
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


def test_continue_after_tool_error_reuses_result_without_reinvoking_tool(tmp_path: Path) -> None:
    echo = EchoTool()

    @dataclass
    class RecoverableFollowupClient:
        calls: int = 0
        received_tools: list[list[dict[str, Any]] | None] = field(default_factory=list)
        context_window: int = 128000

        def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
            return ""

        def stream(
            self,
            messages: list[dict[str, Any]],
            *,
            tools: list[dict[str, Any]] | None = None,
            **overrides: Any,
        ) -> Iterator[LLMStreamEvent]:
            self.calls += 1
            self.received_tools.append(tools)
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
            yield LLMTextDelta(text="已根据保存的结果整理完成。")
            yield LLMTurnDone(stop_reason="end_turn")

    fake = RecoverableFollowupClient()
    conv = _make_conversation(
        tmp_path,
        fake_llm=cast(LLMClient, fake),
        registry=ToolRegistry([echo]),
    )

    with pytest.raises(LLMNetworkError):
        list(conv.stream("please use the tool", run_id="failed-run"))
    conv.append_run_error_event(
        code="upstream_transient",
        message="模型服务连接不稳定",
        recoverable=True,
        source="test",
        run_id="failed-run",
    )

    events = list(conv.continue_after_tool_error(run_id="continue-run"))

    assert echo.call_count == 1
    assert fake.received_tools[-1] is None
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

    monkeypatch.setattr("llm_providers.client.litellm.completion", fake_completion)
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

    tool_reqs = [ev for ev in events if isinstance(ev, ToolCallRequest)]
    assert len(tool_reqs) == 1
    assert tool_reqs[0].args == {"limit": 10}

    request_event = next(ev for ev in conv.session.events if ev.type == "tool_call_request")
    assert request_event.payload["args"] == {"limit": 10}


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
    first = next(stream)
    done = next(stream)
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

    # 兜底调用（最后一次 LLM 调用）messages 应包含 "不要再调用任何工具" 的 system msg
    finalization_messages = fake.received[-1]
    system_msgs = [m for m in finalization_messages if m.get("role") == "system"]
    assert any("不要再调用任何工具" in m["content"] for m in system_msgs)


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

    class SearchTool:
        name: ClassVar[str] = "web_search"
        description: ClassVar[str] = "test search"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult("search result", meta={"source_urls": [target]})

    class FetchProvider:
        name: ClassVar[str] = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent:
            self.calls += 1
            return ExtractedContent(url, url, "verified source")

    provider = FetchProvider()
    registry = ToolRegistry([SearchTool(), WebFetchTool(provider)])
    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "search-1", "web_search", '{"query":"x"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [
                LLMToolCallDelta(0, "fetch-1", "web_fetch", json.dumps({"url": target})),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="verified"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=registry)
    events = list(conv.stream("check"))
    results = [event for event in events if isinstance(event, ToolCallResult)]
    assert provider.calls == 1
    assert [event.is_error for event in results] == [False, False]
    search_event = next(
        event
        for event in conv.session.events
        if event.type == "tool_call_result" and event.payload["tool_name"] == "web_search"
    )
    assert "source_urls" not in search_event.meta.get("extra", {})


def test_web_fetch_rejects_url_from_no_current_turn_source(tmp_path: Path) -> None:
    class FetchProvider:
        name: ClassVar[str] = "fake"

        def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent:
            raise AssertionError("unauthorized provider call")

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
            [LLMTextDelta(text="cannot read"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    registry = ToolRegistry([WebFetchTool(FetchProvider())])
    events = list(_make_conversation(tmp_path, fake_llm=fake, registry=registry).stream("check"))
    result = next(event for event in events if isinstance(event, ToolCallResult))
    assert result.is_error
    assert "不是用户本轮提供" in result.text


def test_datetime_tool_receives_hidden_trusted_runtime_without_persisting_it(
    tmp_path: Path,
) -> None:
    seen_args: list[dict[str, Any]] = []

    class CaptureDateTool:
        name: ClassVar[str] = "datetime_calculate"
        description: ClassVar[str] = "capture runtime"
        parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

        def invoke(self, args: dict[str, Any]) -> ToolResult:
            seen_args.append(args)
            return ToolResult("ok")

    fake = _ScriptedLLMClient(
        script=[
            [
                LLMToolCallDelta(0, "date-1", "datetime_calculate", '{"date":"$today"}'),
                LLMTurnDone(stop_reason="tool_use"),
            ],
            [LLMTextDelta(text="done"), LLMTurnDone(stop_reason="end_turn")],
        ]
    )
    conv = _make_conversation(tmp_path, fake_llm=fake, registry=ToolRegistry([CaptureDateTool()]))
    list(conv.stream("今天星期几"))
    assert "__agent_friend_runtime_now" in seen_args[0]
    request = next(event for event in conv.session.events if event.type == "tool_call_request")
    assert "__agent_friend_runtime_now" not in request.payload["args"]


def test_repeated_identical_failures_stop_as_no_progress(tmp_path: Path) -> None:
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
    assert done.stop_reason == "no_progress"
    assert done.total_tool_calls == 3


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
        if message.get("role") == "system" and "工具运行预算即将到达边界" in message["content"]
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

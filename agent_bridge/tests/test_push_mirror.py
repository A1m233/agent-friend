"""014 单测：Pull 路径（``/ag-ui/run``、``/v1/chat/completions``）镜像复制到
push 通道订阅者。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §8.5 +
requirement.md AC-7 / R-4.6.4（pull 路径不退化 + 镜像可见）。

测试策略：
- 起一个侧 thread 跑 asyncio loop 接 :class:`Subscriber`（kinds=user_turn）
- 用 ``TestClient`` 同步 POST pull 端点——TestClient 默认读完整个响应 body，
  encoder generator 跑完所有 ConversationEvent + 同步喂 ``fan_out_event``，
  侧 loop subscriber 的 queue 积累 envelope
- 用 ``run_coroutine_threadsafe`` 跨 thread 拉一个 envelope 断言
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from agent.runtime import (
    AgentRuntime,
    PushEnvelope,
    Subscriber,
)
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    make_default_registry,
)
from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
)

if TYPE_CHECKING:
    pass


# ----- 辅助：scripted LLM + side event loop -----


@dataclass
class _ScriptedLLMClient:
    """与 agent 单测同模式的 scripted LLM。"""

    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    turn_idx: int = 0
    context_window: int = 128000

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:  # pragma: no cover
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


@contextmanager
def _side_event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True, name="SideLoop")
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)
        loop.close()


def _await_envelope(sub: Subscriber, *, timeout: float = 3.0) -> PushEnvelope:
    fut = asyncio.run_coroutine_threadsafe(sub.queue.get(), sub.loop)
    return fut.result(timeout=timeout)


def _drain_envelopes(sub: Subscriber, *, n: int, timeout: float = 3.0) -> list[PushEnvelope]:
    """拉 n 条 envelope（按顺序）。"""
    out: list[PushEnvelope] = []
    for _ in range(n):
        out.append(_await_envelope(sub, timeout=timeout))
    return out


# ----- BridgeRuntime fixture：scripted LLM + 启用 agent_runtime -----


def _build_runtime(
    tmp_path: Path,
    *,
    llm_script: list[list[LLMStreamEvent]],
) -> BridgeRuntime:
    settings = BridgeSettings(
        sessions_dir=tmp_path / "sessions",
        memory_enabled=False,
    )
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()

    fake_llm = _ScriptedLLMClient(script=llm_script)

    def _llm_factory(model: str) -> LLMClient:
        return cast(LLMClient, fake_llm)

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    persistent_session_manager = SessionManager(
        store=persistent_store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
    )

    # AgentRuntime 装在 BridgeRuntime 上——但本测试不通过 dispatch 触发，
    # 只通过 pull encoder 镜像 fan_out；conversation_factory 给个安全 noop
    from agent import Conversation as _Conv

    agent_runtime = AgentRuntime(
        conversation_factory=lambda sid: cast(_Conv, None),
    )

    return BridgeRuntime(
        settings=settings,
        persistent_store=persistent_store,
        transient_store=persistent_store,
        persistent_session_manager=persistent_session_manager,
        catalog=catalog,
        tool_registry=tool_registry,
        prompt_builder_factory=_prompt_factory,
        default_persona=catalog.find_by_name("default").name,
        default_model="deepseek/deepseek-chat",
        agent_runtime=agent_runtime,
    )


def _ag_ui_payload(thread_id: str, user_text: str) -> dict[str, Any]:
    """构造一个最小可用的 AG-UI RunAgentInput payload。"""
    return {
        "thread_id": thread_id,
        "run_id": "run-1",
        "messages": [{"id": "u-1", "role": "user", "content": user_text}],
        "tools": [],
        "context": [],
        "forwarded_props": {},
        "state": {},
    }


# ===== AG-UI pull → push 镜像 =====


def test_ag_ui_pull_mirrors_user_turn_to_push_listener(tmp_path: Path) -> None:
    """POST /ag-ui/run 跑一轮 → push subscriber 应收到一个 user_turn envelope。"""
    runtime = _build_runtime(
        tmp_path,
        llm_script=[[LLMTextDelta(text="hi"), LLMTurnDone(stop_reason="end_turn")]],
    )
    assert runtime.agent_runtime is not None

    with _side_event_loop() as loop:
        sub = Subscriber(loop=loop, accept_kinds=frozenset({"user_turn"}))
        runtime.agent_runtime.listeners.register(sub)

        app = create_app_with_runtime(runtime)
        client = TestClient(app)
        resp = client.post(
            "/ag-ui/run",
            json=_ag_ui_payload("test-thread", "hello"),
        )
        # SSE body 应是 200 + RUN_STARTED + 流 + RUN_FINISHED
        assert resp.status_code == 200

        # subscriber 应在 fan_out 完成后立即拿到 envelope（无需 await SSE 流）
        env = _await_envelope(sub, timeout=3.0)
        assert env.kind == "user_turn"
        assert env.session_id == "test-thread"  # AG-UI thread_id 即 session_id
        assert env.source_kind is None
        assert env.seq == 1
        # events 含 TextDelta + TurnDone
        types = [e["type"] for e in env.events]
        assert "text_delta" in types
        assert "done" in types


def test_ag_ui_pull_with_no_user_turn_subscriber_not_mirrored(tmp_path: Path) -> None:
    """订阅 kinds=agent_turn 时 pull 触发的 user_turn 不应送达。"""
    runtime = _build_runtime(
        tmp_path,
        llm_script=[[LLMTextDelta(text="x"), LLMTurnDone(stop_reason="end_turn")]],
    )
    assert runtime.agent_runtime is not None

    with _side_event_loop() as loop:
        sub = Subscriber(loop=loop, accept_kinds=frozenset({"agent_turn"}))
        runtime.agent_runtime.listeners.register(sub)

        app = create_app_with_runtime(runtime)
        client = TestClient(app)
        resp = client.post("/ag-ui/run", json=_ag_ui_payload("t1", "hi"))
        assert resp.status_code == 200

        # subscriber 不应有任何 envelope——用短超时验证
        fut = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(sub.queue.get(), timeout=0.3),
            loop,
        )
        try:
            fut.result(timeout=1.0)
        except TimeoutError:
            pass  # 期望路径
        else:
            raise AssertionError("agent_turn 订阅者不应收到 user_turn envelope")


def test_ag_ui_pull_no_agent_runtime_no_mirror_no_crash(tmp_path: Path) -> None:
    """agent_runtime=None 时 pull 路径行为完全不变——不抛、不阻塞、SSE 正常。"""
    runtime_with = _build_runtime(
        tmp_path,
        llm_script=[[LLMTextDelta(text="z"), LLMTurnDone(stop_reason="end_turn")]],
    )
    # 显式构造没有 agent_runtime 的 runtime
    import dataclasses

    runtime = dataclasses.replace(runtime_with, agent_runtime=None)

    app = create_app_with_runtime(runtime)
    client = TestClient(app)
    resp = client.post("/ag-ui/run", json=_ag_ui_payload("t-noar", "x"))
    assert resp.status_code == 200

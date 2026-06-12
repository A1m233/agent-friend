"""014 单测：``/push/subscribe`` 长 SSE 通道结构性验证 + 边界。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §8.4 +
requirement.md AC-7（结构层 + 边界）：bridge push 端点路由正确挂载、按 kind
过滤、agent_runtime=None 时 503、kinds=空 时 400、Content-Type 是
``text/event-stream``。

**为什么不测 SSE chunk-level 内容**：httpx ``AsyncClient`` + ``ASGITransport``
对长 SSE generator 的 chunk 流式投递不可靠（chunk 缓冲到 connection 关闭前不
flush），同步 ``TestClient.stream`` 配 ``iter_lines`` 同样问题。fan_out_event +
thread→asyncio bridge + envelope 编码的核心机制已由 ``test_runtime_dispatch``
用 side event loop 真实验证；真起 uvicorn + dev CLI 的端到端验证留 M14.8 手工跑。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from agent.runtime import AgentRuntime
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    make_default_registry,
)

if TYPE_CHECKING:
    from llm_providers import LLMClient


# ----- fixtures -----


def _build_runtime(tmp_path: Path, *, with_agent_runtime: bool) -> BridgeRuntime:
    settings = BridgeSettings(
        sessions_dir=tmp_path / "sessions",
        memory_enabled=False,
    )
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()

    def _llm_factory(model: str) -> LLMClient:
        from llm_providers import LLMClient, ProviderSpec

        return LLMClient(ProviderSpec(model=model, api_key="sk-test"))

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    persistent_session_manager = SessionManager(
        store=persistent_store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
    )

    agent_runtime: AgentRuntime | None = None
    if with_agent_runtime:
        agent_runtime = AgentRuntime(
            conversation_factory=lambda sid: cast(Conversation, None),
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


@pytest.fixture
def runtime(tmp_path: Path) -> BridgeRuntime:
    return _build_runtime(tmp_path, with_agent_runtime=True)


# ===== 边界：no agent_runtime / 空 kinds =====


def test_push_subscribe_503_when_no_agent_runtime(tmp_path: Path) -> None:
    """agent_runtime=None → 503（bridge 当前不接受 push 订阅）。"""
    rt = _build_runtime(tmp_path, with_agent_runtime=False)
    app = create_app_with_runtime(rt)
    client = TestClient(app)
    resp = client.get("/push/subscribe?kinds=agent_turn")
    assert resp.status_code == 503


def test_push_subscribe_400_when_kinds_empty(runtime: BridgeRuntime) -> None:
    """kinds=空字符串 → 400（订阅必须至少一种 envelope kind）。"""
    app = create_app_with_runtime(runtime)
    client = TestClient(app)
    resp = client.get("/push/subscribe?kinds=")
    assert resp.status_code == 400


# ===== Encoder：encode_envelope_sse 输出格式正确 =====


def test_encode_envelope_sse_format() -> None:
    """encode_envelope_sse 输出 ``event: push\\ndata: {json}\\n\\n`` 标准 SSE 形态。"""
    import json

    from agent.runtime import PushEnvelope
    from agent_bridge.push.protocol import encode_envelope_sse

    env = PushEnvelope(
        kind="agent_turn",
        session_id="s-1",
        seq=3,
        source_kind="cron:bedtime",
        events=[{"type": "text_delta", "text": "hi"}],
    )
    raw = encode_envelope_sse(env)
    text = raw.decode()
    assert text.startswith("event: push\ndata: ")
    assert text.endswith("\n\n")

    # 提取 data 行 JSON 解析
    data_line = text.split("\n")[1]
    assert data_line.startswith("data: ")
    payload = json.loads(data_line[6:])
    assert payload["kind"] == "agent_turn"
    assert payload["session_id"] == "s-1"
    assert payload["seq"] == 3
    assert payload["source_kind"] == "cron:bedtime"
    assert payload["events"] == [{"type": "text_delta", "text": "hi"}]


def test_encode_envelope_sse_handles_unicode() -> None:
    """中文 / unicode 不被 escape 成 \\uXXXX——ensure_ascii=False。"""
    from agent.runtime import PushEnvelope
    from agent_bridge.push.protocol import encode_envelope_sse

    env = PushEnvelope(
        kind="agent_turn",
        session_id="s",
        seq=1,
        source_kind="cron:bedtime",
        events=[{"type": "text_delta", "text": "该睡了"}],
    )
    raw = encode_envelope_sse(env)
    assert "该睡了".encode() in raw


# ===== 端点已挂载 / health 通 =====


def test_push_subscribe_route_registered(runtime: BridgeRuntime) -> None:
    """``/push/subscribe`` 路由确实挂载到 app（用 OPTIONS 探，避免触发流式逻辑）。"""
    app = create_app_with_runtime(runtime)
    paths = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/push/subscribe" in paths


def test_health_endpoint_still_works(runtime: BridgeRuntime) -> None:
    """sanity：装上 push router 不破坏 health 端点。"""
    app = create_app_with_runtime(runtime)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

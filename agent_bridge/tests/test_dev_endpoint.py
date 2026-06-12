"""014 单测：``/dev/fire-source`` 端点行为（仅 ``dev_mode=True`` 时存在）。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §9 +
requirement.md AC-3（PreToolUse 之外的 dev 端点维度）。

dev_mode=False 时端点不挂载 → 404；dev_mode=True 时按 source_name 找
对应 source 调 fire_now，或 404 / 400 / 503 兜底。
"""

from __future__ import annotations

import queue
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from agent.runtime import AgentRuntime, SystemTriggerEvent
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

if TYPE_CHECKING:
    from llm_providers import LLMClient


# ----- 测试 fixture：含可选 agent_runtime 的 BridgeRuntime -----


class _RecordingSource:
    """fire_now 仅记录调用；不真起 thread。"""

    name: ClassVar[str] = "test:recording"

    def __init__(self) -> None:
        self.fire_count = 0
        self._inbox: queue.Queue[Any] | None = None

    def start(self, inbox: queue.Queue[Any]) -> None:
        self._inbox = inbox

    def stop(self) -> None:
        pass

    def fire_now(self) -> None:
        self.fire_count += 1
        if self._inbox is not None:
            self._inbox.put(
                SystemTriggerEvent(
                    session_id="test-session",
                    source_kind=self.name,
                    system_prompt_addendum="test",
                )
            )


def _build_runtime(
    tmp_path: Path,
    *,
    dev_mode: bool,
    with_agent_runtime: bool = True,
) -> tuple[BridgeRuntime, _RecordingSource | None]:
    settings = BridgeSettings(
        sessions_dir=tmp_path / "sessions",
        memory_enabled=False,
        dev_mode=dev_mode,
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
    recording_source: _RecordingSource | None = None
    if with_agent_runtime:
        # 测试用最小 conversation_factory；只在 _dispatch 真跑时才被调
        from typing import cast as _cast

        from agent import Conversation

        agent_runtime = AgentRuntime(
            conversation_factory=lambda sid: _cast(Conversation, None),
        )
        recording_source = _RecordingSource()
        agent_runtime.register_source(recording_source)

    return (
        BridgeRuntime(
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
        ),
        recording_source,
    )


# ===== dev_mode=False：端点不挂载 =====


def test_dev_mode_false_returns_404(tmp_path: Path) -> None:
    runtime, _ = _build_runtime(tmp_path, dev_mode=False)
    app = create_app_with_runtime(runtime)
    client = TestClient(app)
    resp = client.post("/dev/fire-source", params={"source_name": "any"})
    assert resp.status_code == 404


# ===== dev_mode=True：端点行为 =====


def test_dev_mode_true_missing_source_name_param_returns_422(tmp_path: Path) -> None:
    runtime, _ = _build_runtime(tmp_path, dev_mode=True)
    app = create_app_with_runtime(runtime)
    client = TestClient(app)
    resp = client.post("/dev/fire-source")
    assert resp.status_code == 422  # FastAPI validation


def test_dev_mode_true_unknown_source_returns_404(tmp_path: Path) -> None:
    runtime, _ = _build_runtime(tmp_path, dev_mode=True)
    app = create_app_with_runtime(runtime)
    client = TestClient(app)
    resp = client.post("/dev/fire-source", params={"source_name": "no-such-source"})
    assert resp.status_code == 404
    assert "no-such-source" in resp.json()["detail"]


def test_dev_mode_true_user_source_fires_via_submit_not_fire_now(tmp_path: Path) -> None:
    """UserSource 不实现 fire_now → 400（提示走 submit）。"""
    from agent.runtime.sources import UserSource

    runtime, _ = _build_runtime(tmp_path, dev_mode=True)
    assert runtime.agent_runtime is not None
    # 显式注册一个 UserSource 让 source_name=user 能找到
    runtime.agent_runtime.register_source(UserSource())
    app = create_app_with_runtime(runtime)
    client = TestClient(app)
    resp = client.post("/dev/fire-source", params={"source_name": "user"})
    # UserSource 没有 fire_now 方法 → 我们的端点返回 400
    assert resp.status_code == 400
    assert "fire_now" in resp.json()["detail"]


def test_dev_mode_true_known_source_triggers_fire_now(tmp_path: Path) -> None:
    """有 fire_now 的 source → 调用 + 返回 200 + status=fired。"""
    runtime, recording = _build_runtime(tmp_path, dev_mode=True)
    assert recording is not None
    # 模拟 lifespan：手动 start 把 source 的 inbox 绑上
    assert runtime.agent_runtime is not None
    runtime.agent_runtime.start()
    try:
        app = create_app_with_runtime(runtime)
        client = TestClient(app)
        resp = client.post("/dev/fire-source", params={"source_name": "test:recording"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "fired", "source": "test:recording"}
        assert recording.fire_count == 1
    finally:
        runtime.agent_runtime.stop(timeout=2.0)


def test_dev_mode_true_no_agent_runtime_returns_503(tmp_path: Path) -> None:
    """agent_runtime=None → /dev/fire-source 返 503。"""
    runtime, _ = _build_runtime(tmp_path, dev_mode=True, with_agent_runtime=False)
    app = create_app_with_runtime(runtime)
    client = TestClient(app)
    resp = client.post("/dev/fire-source", params={"source_name": "anything"})
    assert resp.status_code == 503

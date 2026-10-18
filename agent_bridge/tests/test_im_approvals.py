from __future__ import annotations

import asyncio
import html
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from agent.approvals import ApprovalBroker, CatastrophicActionPolicy
from agent.command_runtime.models import CommandApprovalDetails, PendingCommandApproval
from agent.filesystem import FilesystemService, ProtectedPathPolicy
from agent.filesystem.models import ApprovalItem, PendingFilesystemApproval
from agent.filesystem.tools import FilesystemOperationTool, RequestFilesystemAccessTool
from agent.sessions.metadata import SessionMetadataStore
from agent.system_prompt import IM_QQ_SURFACE
from agent_bridge.protocols.im.approvals import IMApprovalService
from agent_bridge.protocols.im.approvals.rendering import approval_text, markdown_literal
from agent_bridge.protocols.im.approvals.service import PAGE_PAYLOAD
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.event import InboundEvent, InboundInteraction
from agent_bridge.protocols.im.provider import IMProvider, OutboundSendResult
from agent_bridge.protocols.im.state import IMBindingKey, IMStateStore

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    Session,
    Tool,
    ToolRegistry,
)
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
)


class Provider:
    type = "qq"
    bind_id = "owner"

    def __init__(self) -> None:
        self.sent: list[OutboundContent] = []
        self.receipt = OutboundSendResult("delivered", "message", controls="accepted")
        self.block: asyncio.Event | None = None

    async def send(self, content: OutboundContent) -> OutboundSendResult:
        self.sent.append(content)
        if self.block is not None:
            await self.block.wait()
        return self.receipt


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.provider = Provider()
        self.typed_provider = cast(IMProvider, self.provider)
        self.state = IMStateStore(tmp_path / "im.json")
        self.key = IMBindingKey("qq", "owner", "c2c", "owner")
        self.state.set_binding(self.key, "session")
        self.exists = True
        self.broker = ApprovalBroker(
            mode_store=SessionMetadataStore(tmp_path / "meta.json"),
            session_exists=lambda _sid: self.exists,
            risk_policy=CatastrophicActionPolicy(path_flavor="windows", home=Path("C:/Users/test")),
        )
        self.service = IMApprovalService(
            self.broker,
            self.state,
            session_title=lambda _sid: "测试会话",
            session_exists=lambda _sid: self.exists,
        )
        self.service.activate(self.typed_provider)
        self.event = InboundEvent(
            event_type="C2C_MESSAGE_CREATE",
            chat_scope="c2c",
            chat_id="owner",
            message_type=0,
            user_id="owner",
            message_id="inbound",
            content="test",
            timestamp=datetime.now(UTC).isoformat(),
        )

    def route(self) -> Any:
        return self.service.run_route(self.typed_provider, self.event, "session", "run")

    async def click(self, payload: str) -> None:
        await self.service.handle(self.typed_provider, interaction(payload))

    def button(self, label: str) -> str:
        return button(self.provider.sent[-1], label)


def interaction(payload: str) -> InboundInteraction:
    return InboundInteraction(
        "click-event",
        "INTERACTION_CREATE",
        "c2c",
        "owner",
        "owner",
        payload,
        reply_event_id="dispatch-event",
    )


def button(content: OutboundContent, label: str) -> str:
    assert content.controls is not None, content.text
    return next(a.payload for row in content.controls.rows for a in row if a.label == label)


def request(kind: str = "filesystem") -> Any:
    if kind == "command":
        return PendingCommandApproval.create(
            session_id="session",
            run_id="run",
            tool_call_id="call",
            reason="读取测试信息",
            details=CommandApprovalDetails(
                cmd="echo test",
                argv=("echo", "test"),
                workdir="C:/work",
                cwd_source="explicit",
                mode="buffered",
                timeout_seconds=10,
                filesystem_access="read_only",
                additional_roots=(),
                host_derived_roots=(),
                network_access="disabled",
                interactive=False,
            ),
        )
    return PendingFilesystemApproval.create(
        session_id="session",
        run_id="run",
        tool_call_id="call",
        reason="读取测试文件",
        items=(ApprovalItem("file", Path("C:/work/test.txt"), "file", frozenset({"read_text"})),),
        actions=(),
    )


async def card(harness: Harness) -> OutboundContent:
    async with asyncio.timeout(3):
        while True:
            for message in harness.provider.sent:
                if message.controls is not None and "等待授权" in message.text:
                    # Let the send receipt activate its token before returning.
                    await asyncio.sleep(0.01)
                    return message
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_mode_confirmation_cancel_replay_exit_and_persistence(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.click(PAGE_PAYLOAD)
    assert h.provider.sent[-1].reply_to_event_id == "dispatch-event"
    await h.click(h.button("本会话免授权"))
    confirm = h.button("确认开启")
    assert "host_user" in h.provider.sent[-1].text
    assert h.broker.mode_state("session").mode == "per_request"
    await h.click(h.button("取消"))
    await h.click(confirm)
    assert h.broker.mode_state("session").mode == "per_request"
    await h.click(PAGE_PAYLOAD)
    await h.click(h.button("本会话免授权"))
    confirm = h.button("确认开启")
    await h.click(confirm)
    assert h.broker.mode_state("session").mode == "session_auto"
    assert (
        SessionMetadataStore(tmp_path / "meta.json").get("session").approval_mode == "session_auto"
    )
    assert h.broker.mode_state("new-session").mode == "per_request"
    await h.click(PAGE_PAYLOAD)
    await h.click(h.button("每次授权"))
    assert h.broker.mode_state("session").mode == "per_request"
    await h.click(confirm)
    assert h.broker.mode_state("session").mode == "per_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["filesystem", "command"])
@pytest.mark.parametrize("allow", [True, False])
async def test_pending_approval_decision_and_replay(tmp_path: Path, kind: str, allow: bool) -> None:
    h = Harness(tmp_path)
    req = request(kind)
    with h.route():
        waiting = asyncio.create_task(
            asyncio.to_thread(h.broker.request, req, timeout_seconds=2, surface="im:qq")
        )
        content = await card(h)
        assert content.text_format == "markdown"
        assert content.controls is not None
        assert [len(row) for row in content.controls.rows] == [2, 1]
        payload = button(content, "仅本次允许" if allow else "拒绝")
        await h.click(payload)
        result = await waiting
        assert result.approved is allow
        await asyncio.sleep(0.01)
        notices = [message for message in h.provider.sent if message.controls is None]
        assert len(notices) == 1
        version = req.version
        await h.click(payload)
        assert req.version == version
        assert "失效" in h.provider.sent[-1].text
    assert not h.broker.surface_available("session", "run", "im:qq")


@pytest.mark.asyncio
async def test_desktop_wins_and_old_qq_decision_cannot_change_result(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    req = request()
    with h.route():
        waiting = asyncio.create_task(
            asyncio.to_thread(h.broker.request, req, timeout_seconds=2, surface="im:qq")
        )
        content = await card(h)
        assert h.broker.decide(
            req.request_id, req.cancelled_decision(), session_id="session", run_id="run"
        )
        await h.click(button(content, "仅本次允许"))
        assert not (await waiting).approved
        assert req.status == "denied"


@pytest.mark.asyncio
async def test_mode_switch_while_pending_unblocks_exact_request(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    req = request()
    with h.route():
        waiting = asyncio.create_task(
            asyncio.to_thread(h.broker.request, req, timeout_seconds=2, surface="im:qq")
        )
        content = await card(h)
        await h.click(button(content, "授权设置"))
        await h.click(h.button("本会话免授权"))
        await h.click(h.button("确认开启"))
        assert (await waiting).approved
        assert req.decision_source == "session_auto"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rebind", "unbind", "replace", "cancel", "delete"])
async def test_route_revocation_and_pending_cleanup(tmp_path: Path, change: str) -> None:
    h = Harness(tmp_path)
    req = request()
    cancelled: list[bool] = []
    with h.route():
        h.service.attach_cancel("session", "run", lambda: cancelled.append(True))
        waiting = asyncio.create_task(
            asyncio.to_thread(h.broker.request, req, timeout_seconds=0.5, surface="im:qq")
        )
        content = await card(h)
        if change == "rebind":
            h.state.set_binding(h.key, "second")
            h.state.set_binding(h.key, "session")
        elif change == "unbind":
            h.state.delete_provider("qq", "owner")
        elif change == "replace":
            h.service.activate(cast(IMProvider, Provider()))
        elif change == "cancel":
            h.broker.cancel_run("session", "run")
        else:
            h.exists = False
            h.broker.cancel_run("session", "run")
        await h.click(button(content, "仅本次允许"))
        result = await waiting
        assert result is None or not result.approved
        assert req.status in {"cancelled", "expired"}
        assert not h.broker.surface_available("session", "run", "im:qq")
    if change in {"rebind", "unbind", "replace"}:
        assert cancelled == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        OutboundSendResult("delivered", "text-only", controls="omitted"),
        OutboundSendResult("unknown"),
        OutboundSendResult("failed"),
    ],
)
async def test_failed_controls_never_enable_mode(
    tmp_path: Path, receipt: OutboundSendResult
) -> None:
    h = Harness(tmp_path)
    h.provider.receipt = receipt
    await h.click(PAGE_PAYLOAD)
    payload = button(h.provider.sent[0], "本会话免授权")
    h.provider.receipt = OutboundSendResult("delivered", "ok", controls="accepted")
    await h.click(payload)
    assert h.broker.mode_state("session").mode == "per_request"
    assert not any(
        "确认开启" in a.label
        for msg in h.provider.sent
        if msg.controls
        for row in msg.controls.rows
        for a in row
    )


@pytest.mark.asyncio
async def test_callback_before_receipt_and_binding_change_during_send(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    block = asyncio.Event()
    h.provider.block = block
    sending = asyncio.create_task(h.click(PAGE_PAYLOAD))
    while not h.provider.sent:
        await asyncio.sleep(0)
    payload = button(h.provider.sent[0], "本会话免授权")
    assert h.service._tokens.resolve(payload) is None
    h.state.set_binding(h.key, "second")
    h.state.set_binding(h.key, "session")
    block.set()
    await sending
    await h.click(payload)
    assert h.broker.mode_state("session").mode == "per_request"


@pytest.mark.asyncio
async def test_mode_version_conflict_and_wrong_operator(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.click(PAGE_PAYLOAD)
    await h.click(h.button("本会话免授权"))
    payload = h.button("确认开启")
    await h.service.handle(h.typed_provider, replace(interaction(payload), operator_id="other"))
    assert h.broker.mode_state("session").mode == "per_request"
    h.broker.set_session_mode("session", mode="session_auto", expected_version=0)
    h.broker.set_session_mode("session", mode="per_request", expected_version=1)
    await h.click(payload)
    assert h.broker.mode_state("session").mode == "per_request"
    assert "已经变化" in h.provider.sent[-1].text


@pytest.mark.asyncio
async def test_long_request_keeps_desktop_pending_without_qq_allow(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    req = request("command")
    req.details = replace(req.details, cmd="x" * 2000)
    with h.route():
        waiting = asyncio.create_task(
            asyncio.to_thread(h.broker.request, req, timeout_seconds=0.15, surface="im:qq")
        )
        assert await waiting is None
        assert req.status == "expired"
    assert h.provider.sent
    assert all(msg.controls is None for msg in h.provider.sent)
    assert "完整详情" in h.provider.sent[0].text


def test_rendering_is_lossless_and_rejects_ambiguous_controls() -> None:
    snapshot = request("command").to_dict()
    encoded = approval_text(snapshot, "[会话](url)")
    text = html.unescape(encoded)
    assert "echo test" in text and "C:/work" in text
    assert "[会话](url)" in text
    assert "文件权限：只读" in text and "网络权限：关闭" in text
    assert '"argv"' not in text and "\\[" not in text
    assert "\\[" not in encoded and "\\(" not in encoded
    literal = r"C:\Temp\[a](b)\test.txt & <tag> $`*_~!"
    assert html.unescape(markdown_literal(literal)) == literal
    assert "\\[" not in markdown_literal(literal)
    with pytest.raises(ValueError):
        markdown_literal("echo\u202etest")
    with pytest.raises(ValueError):
        approval_text(request().to_dict(), "x" * 2000)


class FileWorkflowLLM:
    context_window = 128_000

    def __init__(self, target: Path) -> None:
        self.target = target
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("only streaming is expected")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            args = {
                "reason": "创建临时验收文件",
                "items": [
                    {
                        "item_id": "test",
                        "locator": {"kind": "path", "path": str(self.target.parent)},
                        "root_kind": "directory",
                        "permissions": ["create"],
                    }
                ],
            }
            name = "request_filesystem_access"
        elif self.calls == 2:
            result = next(item for item in reversed(request.messages) if item.get("role") == "tool")
            payload = json.loads(result["content"])
            if payload.get("status") != "granted":
                yield LLMTextDelta("未获授权，不执行。")
                yield LLMTurnDone(stop_reason="end_turn")
                return
            args = {
                "filesystem_scope_id": payload["scopes"][0]["filesystem_scope_id"],
                "relative_path": self.target.name,
                "content": "approved exact write",
            }
            name = "filesystem_write_text"
        else:
            yield LLMTextDelta("已完成测试文件写入。")
            yield LLMTurnDone(stop_reason="end_turn")
            return
        yield LLMToolCallDelta(
            index=0,
            tool_call_id=f"call-{self.calls}",
            tool_name=name,
            args_json_delta=json.dumps(args),
        )
        yield LLMTurnDone(stop_reason="tool_use")


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [True, False])
async def test_real_conversation_file_tool_roundtrip_via_qq_control(
    tmp_path: Path, allow: bool
) -> None:
    h = Harness(tmp_path)
    session = Session.new(
        title="QQ test",
        persona="default",
        model="deepseek/deepseek-chat",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    store = JsonlSessionStore(tmp_path / "sessions")
    store.create(session)
    h.state.set_binding(h.key, session.session_id)
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    policy = ProtectedPathPolicy(
        product_roots=((tmp_path / "product").resolve(),), personal_skill_roots=()
    )
    target = tmp_path / "write-test.txt"
    llm = FileWorkflowLLM(target)
    conversation = Conversation(
        session=session,
        store=store,
        llm_client=llm,
        context_manager=NaiveContextManager(),
        catalog=catalog,
        prompt_builder=MarkdownPromptBuilder(
            persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog
        ),
        prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
            persona_id=persona_id, catalog=catalog
        ),
        tool_registry=ToolRegistry(
            [
                RequestFilesystemAccessTool(h.broker, policy),
                cast(
                    Tool,
                    FilesystemOperationTool("filesystem_write_text", FilesystemService(policy)),
                ),
            ]
        ),
    )
    with h.service.run_route(h.typed_provider, h.event, session.session_id, "run"):
        waiting = asyncio.create_task(
            asyncio.to_thread(
                lambda: list(
                    conversation.stream(
                        "创建测试文件",
                        surface=IM_QQ_SURFACE,
                        run_id="run",
                    )
                )
            )
        )
        content = await card(h)
        assert "新建" in content.text
        await h.click(button(content, "仅本次允许" if allow else "拒绝"))
        await asyncio.wait_for(waiting, 5)
    assert target.exists() is allow
    if allow:
        assert target.read_text(encoding="utf-8") == "approved exact write"
    results = [event for event in session.events if event.type == "tool_call_result"]
    assert len(results) == (2 if allow else 1)
    assert results[0].payload["is_error"] is not allow

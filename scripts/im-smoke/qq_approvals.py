"""Bounded live QQ approval menu probe; isolated state, no LLM or user-session writes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from agent.approvals import ApprovalBroker
from agent.paths import user_data_dir
from agent.sessions.metadata import SessionMetadataStore
from agent_bridge.protocols.im.adapters.qq import QQAdapter
from agent_bridge.protocols.im.approvals import IMApprovalService
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.credentials import CredentialStore
from agent_bridge.protocols.im.event import InboundEvent, InboundInteraction
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.protocols.im.runtime import IMRuntime
from agent_bridge.protocols.im.state import IMBindingKey, IMStateStore

ROOT = Path(__file__).resolve().parents[2]


def ref(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


async def run(args: argparse.Namespace) -> int:
    credentials = CredentialStore(user_data_dir() / "im_credentials")
    matches = [c for c in credentials.list_all() if c.im_type == "qq"]
    if args.bind_ref:
        matches = [c for c in matches if ref(c.user_openid or c.bind_id) == args.bind_ref]
    print(
        json.dumps({"qq_binding_refs": [ref(c.user_openid or c.bind_id) for c in matches]}),
        flush=True,
    )
    if args.preflight:
        return 0 if len(matches) == 1 else 2
    if len(matches) != 1 or not args.allow_send or args.output is None:
        raise ValueError("require one binding, --allow-send and a new --output")
    output = args.output.resolve()
    if output.exists():
        raise ValueError("refusing to overwrite evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = sorted((ROOT / "agent_bridge/src/agent_bridge/protocols/im").rglob("*.py"))
    sources.extend((ROOT / "agent/src/agent/approvals").rglob("*.py"))
    sources.append(Path(__file__).resolve())
    report: dict[str, Any] = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
        },
        "llm_calls": 0,
        "scope": "live QQ adapter + approval service, isolated binding and metadata; no desktop runtime",
        "case": args.case,
        "client_observation": "pending",
        "events": [],
    }

    def record(kind: str, **values: Any) -> None:
        entry = {"at": datetime.now(UTC).isoformat(), "kind": kind, **values}
        report["events"].append(entry)
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output)
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    class ObservedProvider(QQAdapter):
        send_count = 0

        async def _on_interaction_event(self, event_type: str, raw: dict[str, Any]) -> None:
            from agent_bridge.protocols.im.adapters.qq_gateway import DISPATCH_EVENT_ID

            record(
                "gateway_identity",
                reply_event_present=bool(raw.get(DISPATCH_EVENT_ID)),
                ack_and_reply_differ=raw.get("id") != raw.get(DISPATCH_EVENT_ID),
            )
            await super()._on_interaction_event(event_type, raw)

        async def send(self, content: OutboundContent) -> OutboundSendResult:
            self.send_count += 1
            if self.send_count > 30:
                raise RuntimeError("live probe message budget exhausted")
            result = await super().send(content)
            record(
                "delivery",
                outcome=result.outcome,
                controls=result.controls,
                error_kind=result.error_kind,
                message_ref=ref(result.provider_message_id or ""),
                text=content.text,
                buttons=[a.label for row in content.controls.rows for a in row]
                if content.controls
                else [],
                proactive=not content.reply_to_event_id and not content.reply_to_message_id,
            )
            return result

    # Capture only structured probe records; SDK logs can contain identity or message payloads.
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(logging.NullHandler())
    logging.getLogger().setLevel(logging.CRITICAL)
    cred = matches[0]
    bind_id = cred.user_openid or cred.bind_id
    with tempfile.TemporaryDirectory(prefix="af163-qq-approval-") as temporary:
        root = Path(temporary)
        state = IMStateStore(root / "im.json")
        key = IMBindingKey("qq", bind_id, "c2c", bind_id)
        session_id = "af163-live-approval-menu"
        state.set_binding(key, session_id)
        broker = ApprovalBroker(mode_store=SessionMetadataStore(root / "metadata.json"))
        service = IMApprovalService(
            broker,
            state,
            session_title=lambda _: "AF163 授权验收（隔离）",
            session_exists=lambda sid: sid == session_id,
        )
        owner = IMRuntime(
            router=MagicMock(),
            credentials=credentials,
            resume_base_dir=root / "resume",
            lock_base_dir=user_data_dir() / "im_provider_locks",
        )
        lock = owner._acquire_provider_lock("qq", bind_id)
        provider = ObservedProvider(cred=cred, resume_base_dir=root / "resume", state_store=state)
        done = asyncio.Event()
        stale_clicked = asyncio.Event()
        callback_lock = asyncio.Lock()

        async def inbound(event: InboundEvent) -> None:
            if event.user_id == bind_id:
                record("inbound_ignored", reason="menu probe uses buttons only")

        async def interaction(event: InboundInteraction) -> None:
            if event.chat_id != bind_id or event.operator_id != bind_id:
                return
            async with callback_lock:
                action = service._tokens.resolve(event.payload)
                action_name = action.kind if action is not None else "menu_or_stale"
                handled = await service.handle(provider, event)
                mode = broker.mode_state(session_id)
                record(
                    "interaction",
                    event_ref=ref(event.interaction_id),
                    action=action_name,
                    handled=handled,
                    mode=mode.mode,
                    version=mode.version,
                )
                if action is None and event.payload.startswith("af:approval:v1:"):
                    stale_clicked.set()
                if mode.version >= 2 and mode.mode == "per_request":
                    done.set()
                elif args.case == "modes" and action_name in {"cancel", "confirm"}:
                    await service.show_page(provider, key, event_id=event.reply_event_id)

        try:
            provider.start(inbound, interaction)
            for _ in range(90):
                if provider.status() == "active":
                    break
                if provider.status() == "error":
                    raise RuntimeError("QQ startup rejected")
                await asyncio.sleep(1)
            else:
                raise TimeoutError("QQ startup not ready")
            service.activate(provider)
            record("ready", binding_ref=ref(bind_id), isolated=True)
            if args.case == "preview":
                from agent.command_runtime.models import (
                    CommandApprovalDetails,
                    PendingCommandApproval,
                )
                from agent_bridge.protocols.im.approvals.rendering import approval_text
                from agent_bridge.protocols.im.content import OutboundAction, OutboundControls

                request = PendingCommandApproval.create(
                    session_id=session_id,
                    run_id="preview",
                    tool_call_id="preview",
                    reason="新版显示预览，不执行任何操作，请只检查文字和按钮是否完整可见",
                    details=CommandApprovalDetails(
                        cmd='echo "AF163 [显示测试]"',
                        argv=(
                            r"C:\Windows\System32\cmd.exe",
                            "/D",
                            "/S",
                            "/C",
                            'echo "AF163 [显示测试]"',
                        ),
                        workdir=r"C:\Temp\AF163\中文目录",
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
                buttons = tuple(
                    OutboundAction(
                        id=str(i), label=label, visited_label="仅预览", payload="af:preview:inert"
                    )
                    for i, label in enumerate(("仅本次允许", "拒绝", "授权设置"))
                )
                preview_text = approval_text(request.to_dict(), "AF163 显示预览")
                receipt = await provider.send(
                    OutboundContent(
                        chat_id=bind_id,
                        chat_scope="c2c",
                        text=preview_text,
                        text_format="markdown",
                        controls=OutboundControls(rows=(buttons[:2], buttons[2:])),
                    )
                )
                record(
                    "completed",
                    preview_only=True,
                    outcome=receipt.outcome,
                    controls=receipt.controls,
                )
                return 0 if receipt.outcome == "delivered" else 2
            if args.case == "pending":
                from qq_approval_cases import file_cases
                from qq_command_cases import command_cases

                if args.codex_executable is None:
                    raise ValueError("pending case requires --codex-executable")
                await file_cases(
                    root, service, provider, session_id, callback_lock, record, pending_auto=True
                )
                if broker.mode_state(session_id).mode != "session_auto":
                    raise RuntimeError("pending case must be resolved by mode switch")
                await command_cases(
                    root,
                    service,
                    provider,
                    session_id,
                    callback_lock,
                    record,
                    args.codex_executable,
                    auto_only=True,
                )
                await service.show_page(provider, key)
                await asyncio.wait_for(done.wait(), timeout=300)
                stale_clicked.clear()
                await provider.send(
                    OutboundContent(
                        chat_id=bind_id,
                        chat_scope="c2c",
                        text="AF163：已切回每次授权。请回到本轮最初的文件授权卡片，点击旧的“仅本次允许”；预期只提示已处理或失效。",
                    )
                )
                await asyncio.wait_for(stale_clicked.wait(), timeout=300)
                if broker.mode_state(session_id).mode != "per_request" or broker.pending():
                    raise RuntimeError("stale callback changed approval state")
                record(
                    "completed",
                    cases="pending-auto,command-auto,stale-button",
                    mode="per_request",
                    client_observation="pending",
                )
                return 0
            if args.case == "files":
                from qq_approval_cases import file_cases

                await file_cases(root, service, provider, session_id, callback_lock, record)
                record("completed", cases="file-allow,file-deny", client_observation="pending")
                return 0
            if args.case == "commands":
                from qq_command_cases import command_cases

                if args.codex_executable is None:
                    raise ValueError(
                        "command case requires --codex-executable from command-runtime ensure"
                    )
                await command_cases(
                    root,
                    service,
                    provider,
                    session_id,
                    callback_lock,
                    record,
                    args.codex_executable,
                )
                record(
                    "completed", cases="command-allow,command-deny", client_observation="pending"
                )
                return 0
            await service.show_page(provider, key)
            first = next((e for e in report["events"] if e["kind"] == "delivery"), None)
            if first is None or first["outcome"] != "delivered" or first["controls"] != "accepted":
                record("not_verified", reason="initial controls not confirmed delivered")
                return 2
            await asyncio.wait_for(done.wait(), timeout=args.wait_seconds)
            persisted = SessionMetadataStore(root / "metadata.json").get(session_id)
            record(
                "completed", persisted_mode=persisted.approval_mode, client_observation="pending"
            )
            return 0
        finally:
            service.deactivate(provider)
            try:
                await provider.stop()
            finally:
                owner._release_provider_lock(lock, "qq", bind_id)
                record("stopped", user_session_writes=0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--allow-send", action="store_true")
    parser.add_argument("--bind-ref")
    parser.add_argument(
        "--case", choices=["modes", "files", "commands", "pending", "preview"], default="modes"
    )
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.wait_seconds <= 900:
        parser.error("--wait-seconds must be between 1 and 900")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(json.dumps({"status": "not_verified", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

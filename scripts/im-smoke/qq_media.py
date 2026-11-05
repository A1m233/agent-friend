"""Send one synthetic proactive caption/image pair to the configured QQ owner.

Real delivery service, IM runtime and QQ HTTP adapter; isolated session/asset
stores and a synthetic online presence. No gateway, desktop or LLM is started.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import tempfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from agent.attachments import AttachmentStore
from agent.paths import user_data_dir
from agent.sessions.events import Event
from agent.sessions.manager import SessionManager
from agent_bridge.protocols.im.adapters.qq import QQAdapter
from agent_bridge.protocols.im.credentials import CredentialStore
from agent_bridge.protocols.im.runtime import IMRuntime
from agent_bridge.protocols.im.state import IMStateStore
from agent_bridge.session_delivery.service import SessionDeliveryService
from qqbot_agent_sdk.api_client import QQApiClient

from agent import JsonlSessionStore

ROOT = Path(__file__).resolve().parents[2]
CAPTION = "【主动回复图文回归 AF-MEDIA】这条配文应与随后发送的彩色测试图一同到达。这是修复验证消息，无需回复。"


def ref(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


async def run(args: argparse.Namespace) -> int:
    credentials = CredentialStore(user_data_dir() / "im_credentials")
    matches = [c for c in credentials.list_all() if c.im_type == "qq"]
    if args.bind_ref:
        matches = [c for c in matches if ref(c.user_openid or c.bind_id) == args.bind_ref]
    if len(matches) != 1:
        raise ValueError("require_one_qq_binding")
    cred = matches[0]
    bind_id = cred.user_openid or cred.bind_id
    bindings = [
        b
        for b in IMStateStore(user_data_dir() / "im_state.json").list_bindings()
        if b.key.provider_bind_id == bind_id
        and b.key.chat_id == bind_id
        and b.key.im_type == "qq"
        and b.key.chat_scope == "c2c"
    ]
    if len(bindings) != 1:
        raise ValueError("require_one_owner_session")
    print(json.dumps({"binding_ref": ref(bind_id), "llm_calls": 0}), flush=True)
    if args.preflight:
        return 0
    if not args.allow_send or args.output is None or args.output.exists():
        raise ValueError("require_allow_send_and_new_output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "scope": __doc__,
        "llm_calls": 0,
        "binding_ref": ref(bind_id),
        "client_observation": "pending",
        "wire": [],
        "source_sha256": hashlib.sha256(
            (ROOT / "agent_bridge/src/agent_bridge/protocols/im/adapters/qq.py").read_bytes()
        ).hexdigest(),
    }

    def persist() -> None:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    class ObservedAPI:
        def __init__(self, api):
            self.api = api

        def __getattr__(self, key):
            return getattr(self.api, key)

        async def post_c2c_message(self, chat, message, **kwargs):
            if len(report["wire"]) >= 2:
                raise RuntimeError("message_budget_exhausted")
            item = {
                "kind": "image" if message.media else "text",
                "requested_at": datetime.now(UTC).isoformat(),
            }
            report["wire"].append(item)
            persist()
            try:
                result = await self.api.post_c2c_message(chat, message, **kwargs)
                item.update(
                    code=result.get("code", 0),
                    has_receipt=bool(result.get("id")),
                    receipt_ref=ref(str(result.get("id", ""))),
                )
                return result
            except Exception as exc:
                item["error_type"] = type(exc).__name__
                raise
            finally:
                persist()

    with tempfile.TemporaryDirectory(prefix="af-qq-media-", ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        binding = bindings[0]
        state = IMStateStore(base / "im.json")
        state.set_binding(binding.key, binding.session_id)
        store = JsonlSessionStore(base / "sessions")
        sessions = SessionManager(store)
        source = sessions.create("default", "fixture/no-llm", origin_kind="inner_world").session_id
        sessions.create("default", "fixture/no-llm", session_id=binding.session_id)
        assets = AttachmentStore(base / "attachments")

        async def chunks():
            yield (ROOT / "scripts/model-connections-smoke/fixtures/colors.webp").read_bytes()

        _, asset = await assets.import_stream(chunks(), display_name="AF-MEDIA-test-colors.webp")
        store.append_event(
            source,
            Event(
                type="assistant_message",
                uuid="fixture-image",
                ts=datetime.now(UTC),
                payload={"content": "Test fixture", "attachments": [asset.to_dict()]},
            ),
        )
        provider = QQAdapter(cred=cred, resume_base_dir=base / "resume", state_store=state)
        async with httpx.AsyncClient(timeout=30.0) as client:
            api = QQApiClient(
                app_id=cred.app_id, client_secret=cred.client_secret, log_tag="media-probe"
            )
            api.setup(client)
            provider._api = ObservedAPI(api)
            provider._http_client = client
            # Only sending is under test: no second gateway or receiver is started.
            provider._set_status("active")
            im = IMRuntime(
                router=Mock(),
                credentials=credentials,
                resume_base_dir=base / "resume",
                state_store=state,
                session_exists=sessions.exists,
            )
            im._providers[("qq", bind_id)] = provider
            im._event_loop = asyncio.get_running_loop()
            runtime = SimpleNamespace(
                settings=SimpleNamespace(sessions_dir=base / "sessions"),
                persistent_session_manager=sessions,
                im_runtime=im,
                attachment_store=assets,
                agent_runtime=SimpleNamespace(try_reserve_session_mutation=lambda _: nullcontext()),
                notification_service=None,
            )
            service = SessionDeliveryService(runtime)
            row = service.submit(
                {
                    "__agent_friend_current_session_id": source,
                    "__agent_friend_run_id": "live-test",
                    "__agent_friend_tool_call_id": "one-send",
                    "target_session_id": binding.session_id,
                    "content": CAPTION,
                    "attachment_asset_ids": [asset.asset_id],
                }
            )
            report["caption"] = CAPTION
            persist()
            await asyncio.to_thread(service.deliver_pending)
            receipt = service.store.get(row["delivery_id"])
            report["delivery"] = {
                k: receipt[k] for k in ("conversation_state", "im_state", "error")
            }
            messages = [
                e for e in sessions.open(binding.session_id).events if e.type == "assistant_message"
            ]
            report["session_result"] = {
                "messages": len(messages),
                "caption_matches": len(messages) == 1 and messages[0].payload["content"] == CAPTION,
                "attachments": len(messages[0].payload["attachments"]) if messages else 0,
            }
            count = len(report["wire"])
            await asyncio.to_thread(service.deliver_pending)
            report["no_duplicate_on_reentry"] = len(report["wire"]) == count
            persist()
            print(json.dumps(report, ensure_ascii=False), flush=True)
            passed = (
                receipt["im_state"] == "delivered"
                and count == 2
                and report["no_duplicate_on_reentry"]
                and report["session_result"]["caption_matches"]
                and report["session_result"]["attachments"] == 1
            )
            return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--allow-send", action="store_true")
    parser.add_argument("--bind-ref")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)  # SDK logs can contain private credentials/URLs.
    try:
        raise SystemExit(asyncio.run(run(args)))
    except Exception as exc:
        print(json.dumps({"status": "not_verified", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

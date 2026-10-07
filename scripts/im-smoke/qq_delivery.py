"""Bounded live QQ delivery probe using synthetic turns and production IM code.

No LLM, no session writes. Requires the existing QQ owner to be stopped and a
human to send AF155-A/B/C from mobile QQ. B deliberately closes the first stream
at the real platform boundary before letting the candidate continue it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from agent.paths import user_data_dir
from agent_bridge.protocols.im.adapters.qq import QQAdapter
from agent_bridge.protocols.im.credentials import CredentialStore
from agent_bridge.protocols.im.router import IMRouter, _IMRunCancelled
from agent_bridge.protocols.im.runtime import IMRuntime

from agent import ContextCompactionStatus, TextDelta, TurnDone

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = (
    "IM reply delivery",
    "IM progress delivery",
    "IM delivery interrupted",
    "IM delivery notice",
    "QQ stream accepted",
    "QQ stream request failed",
)


def ref(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


class Capture(logging.Handler):
    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__()
        self.records = records

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.name.startswith("agent_bridge.") and message.startswith(PREFIXES):
            self.records.append({"at": time.time(), "event": message})
            print(message, flush=True)


class WireObserver:
    def __init__(
        self, stream: Any, case: str, first: threading.Event, records: list[dict[str, Any]]
    ) -> None:
        self.stream = stream
        self.api, self.case, self.first, self.records = stream._api, case, first, records
        self.calls = 0

    async def request(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        self.calls += 1
        if self.calls > 16:
            raise RuntimeError("smoke request bound exceeded")
        try:
            result = await self.api.request(method, path, payload)
        except Exception as exc:
            self.records.append(
                {
                    "case": self.case,
                    "wire": "rejected_or_unknown",
                    "index": payload["index"],
                    "error_type": type(exc).__name__,
                }
            )
            raise
        self.records.append(
            {
                "case": self.case,
                "wire": "returned",
                "index": payload["index"],
                "state": payload["input_state"],
                "chars": len(payload["content_raw"]),
                "id_ref": ref(str(result.get("id", ""))),
            }
        )
        if payload["index"] == 0:
            if self.case == "B":
                # A real DONE request, not a fabricated failure response. The local
                # candidate state does not learn the terminal state. Advance only
                # its index past our fixture, so the next request is not a replay.
                closed = dict(payload, stream_msg_id=result["id"], index=1, input_state=10)
                await self.api.request(method, path, closed)
                self.stream._next_index = 1
                self.records.append(
                    {
                        "case": self.case,
                        "fixture": "server_done_accepted",
                        "next_index_after_return": 2,
                    }
                )
            self.first.set()
        return result


class ProbeProvider(QQAdapter):
    case = ""

    def configure_case(self, case: str, records: list[dict[str, Any]]) -> None:
        self.case, self.records = case, records
        self.first = threading.Event()
        self.send_count = 0

    def open_text_stream(self, context: Any) -> Any:
        stream = super().open_text_stream(context)
        if stream is not None:
            stream._api = WireObserver(stream, self.case, self.first, self.records)
        return stream

    async def send(self, content: Any) -> Any:
        self.send_count += 1
        if self.send_count > 4:
            raise RuntimeError("smoke static message bound exceeded")
        result = await super().send(content)
        self.records.append(
            {
                "case": self.case,
                "static": result.outcome,
                "text": content.text,
                "error_kind": result.error_kind,
                "id_ref": ref(result.provider_message_id or ""),
            }
        )
        return result


def conversation(case: str, first: threading.Event) -> Any:
    def generate(_text: str, **_kwargs: Any) -> Any:
        if case == "A":
            yield ContextCompactionStatus(activity_id="af155-a", phase="started")
            time.sleep(3)
            yield ContextCompactionStatus(activity_id="af155-a", phase="completed")
            yield TextDelta(text="【AF155-A】整理后的正文已出现。")
            yield TurnDone()
            return
        yield TextDelta(text=f"【AF155-{case}】第一段已发送。")
        if not first.wait(timeout=30):
            raise RuntimeError("first real QQ receipt not confirmed")
        time.sleep(1)
        if case == "C":
            raise _IMRunCancelled()
        yield TextDelta(text="第二段通过新消息续接，测试完成。")
        yield TurnDone()

    conv = MagicMock()
    conv.stream.side_effect = generate
    return conv


async def run(args: argparse.Namespace) -> int:
    source_files = sorted((ROOT / "agent_bridge/src/agent_bridge/protocols/im").rglob("*.py"))
    source_files.append(Path(__file__).resolve())
    records: list[dict[str, Any]] = [
        {
            "source_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source_files
            },
            "llm_calls": 0,
        }
    ]
    credentials = CredentialStore(user_data_dir() / "im_credentials")
    matches = [
        c
        for c in credentials.list_all()
        if c.im_type == "qq" and ref(c.user_openid or c.bind_id) == args.bind_ref
    ]
    if len(matches) != 1:
        print(json.dumps({"error": "binding_not_unique", "matches": len(matches)}))
        return 2
    print(
        json.dumps(
            {
                "binding_ref": args.bind_ref,
                "source_root": str(ROOT),
                "head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "llm_calls": 0,
                "cases": args.cases,
            }
        ),
        flush=True,
    )
    if args.preflight:
        return 0
    if not args.allow_send or args.output is None:
        raise ValueError("live probe requires --allow-send and --output")
    output = args.output.resolve()
    if output.exists():
        raise ValueError("refusing to overwrite evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    bridge = MagicMock()
    bridge.assistant_event_after.return_value = None
    bridge.assistant_events_after.return_value = []
    router = IMRouter(session_bridge=bridge, default_persona="smoke", default_model="unused")
    done = asyncio.Event()
    seen: set[str] = set()
    in_turn = asyncio.Lock()
    capture = Capture(records)
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.INFO)
    with tempfile.TemporaryDirectory(prefix="af155-qq-smoke-") as temp:
        owner = IMRuntime(
            router=router,
            credentials=credentials,
            resume_base_dir=Path(temp) / "resume",
            lock_base_dir=user_data_dir() / "im_provider_locks",
        )
        cred = matches[0]
        bind_id = cred.user_openid or cred.bind_id
        lock = owner._acquire_provider_lock("qq", bind_id)
        provider = ProbeProvider(cred=cred, resume_base_dir=Path(temp) / "resume")

        async def inbound(event: Any) -> None:
            if event.chat_id != bind_id or event.content.strip() not in {
                f"AF155-{case}" for case in args.cases
            }:
                print("[smoke] ignored non-test inbound", flush=True)
                return
            async with in_turn:
                case = event.content.strip()[-1]
                if case in seen:
                    return
                seen.add(case)
                provider.configure_case(case, records)
                bridge.bind_persistent.return_value = conversation(case, provider.first)
                print(f"[smoke] CASE {case} START", flush=True)
                try:
                    await router.handle_inbound(
                        "qq", event, provider, session_id=f"af155-smoke-{case}"
                    )
                finally:
                    output.write_text(
                        json.dumps(
                            {"seen": sorted(seen), "records": records}, ensure_ascii=False, indent=2
                        ),
                        encoding="utf-8",
                    )
                print(f"[smoke] CASE {case} END", flush=True)
                if seen == set(args.cases):
                    done.set()

        try:
            provider.start(inbound)
            for _ in range(90):
                if provider.status() == "active":
                    print(
                        "[smoke] READY: send "
                        + ", then ".join(f"AF155-{case}" for case in args.cases)
                        + " from mobile QQ",
                        flush=True,
                    )
                    break
                if provider.status() == "error":
                    raise RuntimeError("QQ startup rejected")
                await asyncio.sleep(1)
            else:
                raise TimeoutError("QQ startup not ready")
            await asyncio.wait_for(done.wait(), timeout=args.wait_seconds)
            return 0
        finally:
            await provider.stop()
            owner._release_provider_lock(lock, "qq", bind_id)
            output.write_text(
                json.dumps(
                    {"seen": sorted(seen), "records": records}, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--allow-send", action="store_true")
    parser.add_argument("--bind-ref", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=600)
    parser.add_argument("--cases", nargs="+", choices=["A", "B", "C"], default=["A", "B", "C"])
    args = parser.parse_args()
    if not 1 <= args.wait_seconds <= 900:
        parser.error("--wait-seconds must be between 1 and 900")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "status": "not_verified"}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

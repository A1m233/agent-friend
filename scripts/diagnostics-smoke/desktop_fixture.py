"""Add large synthetic sessions/attachments to an explicitly owned acceptance fixture."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent.attachments.store import AttachmentStore
from agent.sessions.events import Event
from agent.sessions.session import Session
from agent.sessions.store import JsonlSessionStore


def augment(root: Path) -> None:
    receipt_path = root / "fixture.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("synthetic") is not True or Path(receipt["root"]).resolve() != root.resolve():
        raise ValueError("expected an owned synthetic fixture")
    if receipt.get("largeSessions"):
        raise ValueError("large fixtures already exist")
    store = JsonlSessionStore(root / "data" / "sessions")
    attachments = AttachmentStore(root / "data" / "attachments")
    sessions = []
    for index in range(2):

        async def chunks(value: int = index):
            for _ in range(256):
                yield bytes([65 + value]) * 65536

        _, ref = asyncio.run(
            attachments.import_stream(chunks(), display_name=f"synthetic-{index}.bin")
        )
        session = Session.new(f"诊断验收 · 大会话 {index + 1}", "default", "test/synthetic")
        store.create(session)
        for _turn in range(20):
            store.append_events(
                session.session_id,
                [
                    Event(
                        type="user_message",
                        uuid=str(uuid4()),
                        ts=datetime.now(UTC),
                        payload={
                            "content": "synthetic " + "u" * 1048576,
                            "attachments": [ref.to_dict()],
                        },
                    ),
                    Event(
                        type="assistant_message",
                        uuid=str(uuid4()),
                        ts=datetime.now(UTC),
                        payload={"content": "synthetic " + "a" * 1048576},
                    ),
                ],
            )
        sessions.append(
            {
                "sessionId": session.session_id,
                "turns": 20,
                "assetId": ref.asset_id,
                "attachmentBytes": ref.size_bytes,
            }
        )
    receipt["largeSessions"] = sessions
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))

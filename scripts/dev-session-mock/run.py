"""Call the dev-only mock session persistence endpoint without any LLM request."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from uuid import uuid4


def persist_offline(args: argparse.Namespace) -> dict[str, object]:
    from agent import (
        Event,
        JsonlSessionStore,
        NotifyingSessionStore,
        SessionChangeBroker,
        SessionRevisionStore,
        session_mutation_context,
        sessions_dir,
    )

    if not args.session_id:
        raise SystemExit("--offline requires --session-id")
    base_dir = sessions_dir()
    store = NotifyingSessionStore(
        JsonlSessionStore(base_dir),
        SessionChangeBroker(SessionRevisionStore(base_dir / "session-revisions.json")),
    )
    event = Event(
        type="user_message" if args.role == "user" else "assistant_message",
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload={
            "content": args.content,
            **({"partial": False} if args.role == "assistant" else {}),
        },
        meta={"dev_mock": True, "offline": True},
    )
    with session_mutation_context(origin="dev_mock", channel="text"):
        store.append_event(args.session_id, event)
    return {
        "status": "persisted_offline",
        "session_id": args.session_id,
        "role": args.role,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("content")
    parser.add_argument("--session-id")
    parser.add_argument("--title", default="Mock external session")
    parser.add_argument("--role", choices=("user", "assistant"), default="assistant")
    parser.add_argument("--url", default="http://127.0.0.1:18800")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.offline:
        print(json.dumps(persist_offline(args), ensure_ascii=False))
        return 0
    query = urllib.parse.urlencode(
        {
            "content": args.content,
            "title": args.title,
            "role": args.role,
            **({"session_id": args.session_id} if args.session_id else {}),
        }
    )
    request = urllib.request.Request(
        f"{args.url.rstrip('/')}/dev/mock-session-event?{query}", method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

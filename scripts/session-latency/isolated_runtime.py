"""Isolated real-model acceptance backend with a conservative pre-send budget."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "serve"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--max-requests", type=int, choices=(4, 6), default=4)
    args = parser.parse_args()
    directory = args.directory.resolve()
    identity = directory / "acceptance-identity.json"
    if args.action == "prepare":
        from agent.paths import personas_dir, sessions_dir

        directory.mkdir(parents=True, exist_ok=False)
        target = directory / "data" / "sessions"
        target.mkdir(parents=True)
        source = sessions_dir() / f"{args.session_id}.jsonl"
        before = source.stat()
        shutil.copyfile(source, target / source.name)
        short_id = "16ef8d12-fb8a-4826-89e0-898931dbe4d5"
        shutil.copyfile(sessions_dir() / f"{short_id}.jsonl", target / f"{short_id}.jsonl")
        shutil.copytree(personas_dir(), directory / "data" / "personas")
        if (source.stat().st_size, source.stat().st_mtime_ns) != (
            before.st_size,
            before.st_mtime_ns,
        ):
            raise RuntimeError("Source changed during copy")
        identity.write_text(
            json.dumps(
                {
                    "sessionId": args.session_id,
                    "sourceBytes": before.st_size,
                    "sourceMtimeNs": before.st_mtime_ns,
                    "port": 18820,
                }
            ),
            encoding="utf-8",
        )
        print(identity.read_text(encoding="utf-8"))
        return
    if json.loads(identity.read_text(encoding="utf-8"))["sessionId"] != args.session_id:
        raise RuntimeError("Isolated profile identity mismatch")
    budget_file = directory / "budget.json"
    if budget_file.exists():
        raise RuntimeError("Budget cannot be reset by restarting this test runtime")
    os.environ.update(
        {
            "AGENT_FRIEND_DATA_DIR": str(directory / "data"),
            "AGENT_FRIEND_LOG_DIR": str(directory / "logs"),
            "AGENT_BRIDGE_PORT": "18820",
            "AGENT_BRIDGE_IM_ENABLED": "false",
            "AGENT_BRIDGE_ENABLE_BEDTIME": "false",
            "AGENT_BRIDGE_ENABLE_IDLE_REFLECTION": "false",
        }
    )
    import httpx

    budget = {
        "maxRequests": args.max_requests,
        "maxInputTokenUpperBound": 1_000_000,
        "reservedRequests": 0,
        "reservedInputTokenUpperBound": 0,
        "attempts": [],
    }
    budget_file.write_text(json.dumps(budget, indent=2), encoding="utf-8")
    lock = threading.Lock()

    def reserve(request: httpx.Request) -> None:
        if request.method != "POST" or request.url.host in {"localhost", "127.0.0.1", "::1"}:
            return
        body = request.read()
        decoded = json.loads(body)
        # Only text requests are bounded by wire bytes. Refuse image/audio inputs.
        serialized = json.dumps(decoded)
        multimodal = any(
            key in serialized for key in ('"image_url"', '"input_image"', '"input_audio"')
        )
        upper = len(body)
        with lock:
            allowed = (
                not multimodal
                and budget["reservedRequests"] < budget["maxRequests"]
                and budget["reservedInputTokenUpperBound"] + upper
                <= budget["maxInputTokenUpperBound"]
            )
            budget["attempts"].append(
                {
                    "path": request.url.path,
                    "wireBytesUpperBound": upper,
                    "allowed": allowed,
                    "multimodal": multimodal,
                }
            )
            if allowed:
                budget["reservedRequests"] += 1
                budget["reservedInputTokenUpperBound"] += upper
            budget_file.write_text(json.dumps(budget, indent=2), encoding="utf-8")
            if not allowed:
                raise RuntimeError("acceptance_budget_exhausted_before_send")

    original_sync = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request

    def sync_send(self: object, request: httpx.Request) -> httpx.Response:
        reserve(request)
        return original_sync(self, request)

    async def async_send(self: object, request: httpx.Request) -> httpx.Response:
        await request.aread()
        reserve(request)
        return await original_async(self, request)

    httpx.HTTPTransport.handle_request = sync_send
    httpx.AsyncHTTPTransport.handle_async_request = async_send
    import uvicorn
    from agent_bridge.app import create_app
    from starlette.middleware.cors import CORSMiddleware

    app = create_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:1420"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=18820, log_level="warning")


if __name__ == "__main__":
    main()

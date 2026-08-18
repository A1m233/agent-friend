"""Deterministic newline-delimited JSON-RPC fixture for command runtime tests."""

from __future__ import annotations

import base64
import json
import sys
import threading
import time

write_lock = threading.Lock()
live: set[str] = set()


def send(value: dict[str, object]) -> None:
    with write_lock:
        sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def notify(method: str, params: dict[str, object]) -> None:
    send({"method": method, "params": params})


def finish(process_id: str, text: bytes, *, delay: float = 0.02) -> None:
    time.sleep(delay)
    if process_id not in live:
        return
    notify(
        "process/output",
        {
            "processId": process_id,
            "stream": "stdout",
            "seq": 1,
            "chunk": base64.b64encode(text).decode("ascii"),
        },
    )
    notify(
        "process/exited",
        {"processId": process_id, "exitCode": 0, "sandboxDenied": False},
    )
    notify("process/closed", {"processId": process_id})
    live.discard(process_id)


for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    params = message.get("params", {})
    request_id = message.get("id")
    if method == "initialize":
        send({"id": request_id, "result": {"protocolVersion": 1}})
    elif method == "initialized":
        continue
    elif method == "process/start":
        process_id = params["processId"]
        command = params["argv"][-1]
        if command == "fake:disconnect":
            sys.stdout.close()
            break
        live.add(process_id)
        sandbox_type = (
            "none" if command == "fake:none-sandbox" or "sandbox" not in params else "windows"
        )
        send({"id": request_id, "result": {"sandboxType": sandbox_type}})
        if command == "fake:buffered":
            threading.Thread(
                target=finish, args=(process_id, b"buffered output"), daemon=True
            ).start()
        elif command == "fake:large":
            threading.Thread(target=finish, args=(process_id, b"x" * 128), daemon=True).start()
    elif method == "fs/getMetadata":
        send(
            {
                "id": request_id,
                "result": {
                    "isDirectory": True,
                    "isFile": False,
                    "isSymlink": False,
                    "createdAtMs": 0,
                    "modifiedAtMs": 0,
                },
            }
        )
    elif method == "process/write":
        process_id = params["processId"]
        if not isinstance(params.get("writeId"), str) or not params["writeId"]:
            send({"id": request_id, "error": {"code": -32602, "message": "missing writeId"}})
            continue
        chunk = base64.b64decode(params["chunk"])
        send({"id": request_id, "result": {"status": "accepted"}})
        threading.Thread(target=finish, args=(process_id, b"input:" + chunk), daemon=True).start()
    elif method == "process/terminate":
        process_id = params["processId"]
        live.discard(process_id)
        send({"id": request_id, "result": {}})
        notify(
            "process/exited",
            {"processId": process_id, "exitCode": 1, "sandboxDenied": False},
        )
        notify("process/closed", {"processId": process_id})
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown"}})

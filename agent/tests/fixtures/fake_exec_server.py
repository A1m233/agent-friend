"""Deterministic newline-delimited JSON-RPC fixture for command runtime tests."""

from __future__ import annotations

import base64
import json
import sys
import threading
import time

write_lock = threading.Lock()
live: set[str] = set()
commands: dict[str, str] = {}
network_requests: dict[object, str] = {}
pending_rejections: dict[str, object] = {}
pending_metadata: list[object] = []


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
    commands.pop(process_id, None)


for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    params = message.get("params", {})
    request_id = message.get("id")
    if method is None and request_id in network_requests:
        process_id = network_requests.pop(request_id)
        decision = message.get("result", {}).get("decision", {}).get("type")
        threading.Thread(
            target=finish, args=(process_id, str(decision).encode()), daemon=True
        ).start()
        continue
    if method == "initialize":
        send({"id": request_id, "result": {"protocolVersion": 1}})
    elif method == "initialized":
        continue
    elif method == "fixture/release-rejections":
        for pending_id in pending_rejections.values():
            send(
                {
                    "id": pending_id,
                    "error": {
                        "code": -32603,
                        "message": "windows sandbox failed: orchestrator_helper_launch_canceled: 1223",
                    },
                }
            )
        pending_rejections.clear()
        notify("fixture/rejections-released", {})
    elif method == "process/start":
        process_id = params["processId"]
        command = params["argv"][-1]
        if command == "fake:disconnect":
            sys.stdout.close()
            break
        if command == "fake:delayed-rejection":
            pending_rejections[process_id] = request_id
            continue
        live.add(process_id)
        commands[process_id] = command
        sandbox_type = (
            "none" if command == "fake:none-sandbox" or "sandbox" not in params else "windows"
        )
        if command == "fake:network":
            network_requests[request_id] = process_id
            send(
                {
                    "id": request_id,
                    "method": "network/policyRequest",
                    "params": {
                        "processId": process_id,
                        "request": {
                            "host": "example.com",
                            "port": 443,
                            "protocol": "https_connect",
                        },
                    },
                }
            )
        send({"id": request_id, "result": {"sandboxType": sandbox_type}})
        if command == "fake:buffered":
            threading.Thread(
                target=finish, args=(process_id, b"buffered output"), daemon=True
            ).start()
        elif command == "fake:image-output":
            threading.Thread(
                target=finish, args=(process_id, b'{"image_paths":["frame.png"]}'), daemon=True
            ).start()
        elif command == "fake:large":
            threading.Thread(target=finish, args=(process_id, b"x" * 128), daemon=True).start()
    elif method == "process/read":
        process_id = params["processId"]
        if process_id in pending_rejections:
            send({"id": request_id, "error": {"code": -32600, "message": "process is starting"}})
        elif process_id in live:
            send(
                {
                    "id": request_id,
                    "result": {
                        "chunks": [],
                        "nextSeq": 1,
                        "exited": False,
                        "exitCode": None,
                        "closed": False,
                    },
                }
            )
        else:
            send({"id": request_id, "error": {"code": -32600, "message": "unknown process"}})
    elif method == "fs/getMetadata":
        if "delay-metadata" in params.get("path", ""):
            pending_metadata.append(request_id)
            notify("fixture/metadata-pending", {})
            continue
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
    elif method == "fixture/release-preparations":
        for pending_id in pending_metadata:
            send({"id": pending_id, "result": {"isDirectory": True}})
        pending_metadata.clear()
        notify("fixture/preparations-released", {})
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
        command = commands.get(process_id)
        if command == "fake:terminate-error":
            send({"id": request_id, "error": {"code": -32603, "message": "terminate failed"}})
            continue
        if command == "fake:terminate-stall":
            send({"id": request_id, "result": {"running": True}})
            continue
        if command == "fake:terminate-missing":
            live.discard(process_id)
            commands.pop(process_id, None)
            send({"id": request_id, "result": {"running": False}})
            continue
        was_running = process_id in live
        live.discard(process_id)
        commands.pop(process_id, None)
        send({"id": request_id, "result": {"running": was_running}})
        if not was_running:
            continue
        notify(
            "process/exited",
            {"processId": process_id, "exitCode": 1, "sandboxDenied": False},
        )
        notify("process/closed", {"processId": process_id})
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "unknown"}})

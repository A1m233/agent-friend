"""Bounded inspection of unsaved connections; no desktop settings or keyring writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import httpx
import yaml
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.lifecycle import BridgeLifecycleController
from agent_bridge.model_connections.routes import register_routes
from agent_bridge.model_connections.runtime import ConnectionRuntime
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime_config import (
    DesktopSettingsReader,
    EnvSource,
    credential_source_for_mode,
    require_secret,
)

ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "deepseek": ("https://api.deepseek.com", "responses", "deepseek-v4-flash", "DEEPSEEK_API_KEY"),
    "kimi": ("https://api.moonshot.cn/v1", "chat_completions", "kimi-k2.6", "KIMI_API_KEY"),
    "cpa": ("http://127.0.0.1:8317/v1", "responses", "gpt-5.6-luna", ""),
}


def run(target: str) -> dict[str, Any]:
    base, protocol, model, key_name = CASES[target]
    if target == "cpa":
        config = yaml.safe_load(
            (Path.home() / ".local/share/cliproxyapi/config.yaml").read_text(encoding="utf-8")
        )
        key = config["api-keys"][0]
    else:
        key = require_secret(credential_source_for_mode("desktop"), key_name)
    endpoint = base + ("/responses" if protocol == "responses" else "/chat/completions")
    requests: list[dict[str, Any]] = []
    sync_send, async_send = (
        httpx.HTTPTransport.handle_request,
        httpx.AsyncHTTPTransport.handle_async_request,
    )

    def observe(request: httpx.Request) -> None:
        expected = base + "/models" if request.method == "GET" else endpoint
        limit = 1 if request.method == "GET" else 3
        if (
            str(request.url) != expected
            or request.method not in {"GET", "POST"}
            or sum(r["method"] == request.method for r in requests) >= limit
        ):
            raise RuntimeError("inspection smoke wire budget exceeded")
        requests.append({"method": request.method, "endpoint": str(request.url)})

    def send(transport: Any, request: httpx.Request) -> httpx.Response:
        observe(request)
        return sync_send(transport, request)

    async def asend(transport: Any, request: httpx.Request) -> httpx.Response:
        observe(request)
        return await async_send(transport, request)

    report: dict[str, Any] = {
        "target": target,
        "model": model,
        "protocol": protocol,
        "requests": requests,
        "passed": False,
    }
    with tempfile.TemporaryDirectory(prefix="af121-draft-probe-") as temp:
        reader = DesktopSettingsReader(Path(temp) / "settings.json")
        runtime = ConnectionRuntime(EnvSource({}), reader)
        app = FastAPI()
        register_routes(
            app, BridgeLifecycleController(cast(BridgeRuntime, None), token="smoke-owner"), runtime
        )
        client = TestClient(app)
        body = {"baseUrl": base, "protocol": protocol, "apiKey": key, "model": model}
        headers = {"x-agent-friend-lifecycle-token": "smoke-owner"}
        try:
            with (
                patch.object(httpx.HTTPTransport, "handle_request", send),
                patch.object(httpx.AsyncHTTPTransport, "handle_async_request", asend),
            ):
                discovered = client.post(
                    "/internal/model-connections/inspect",
                    json={**body, "mode": "discover"},
                    headers=headers,
                )
                discovered.raise_for_status()
                result = discovered.json()
                report["discovery"] = {
                    "error": result.get("error"),
                    "selected": next(
                        (m for m in result.get("models", []) if m["id"] == model), None
                    ),
                }
                if result.get("error"):
                    return report
                response = client.post(
                    "/internal/model-connections/inspect",
                    json={**body, "mode": "probe"},
                    headers=headers,
                )
                response.raise_for_status()
                report["probe"] = response.json()
            report["settings_untouched"] = not reader.path.exists()
            report["passed"] = (
                report["settings_untouched"]
                and report["discovery"]["selected"] is not None
                and all(
                    report["probe"][kind]["status"] == "passed" for kind in ("generation", "tools")
                )
            )
        except Exception as error:
            report["error"] = {"type": type(error).__name__}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--target", choices=[*CASES, "all"], default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("requires --allow-llm; at most three generations per target")
    report: dict[str, Any] = {
        "runs": [],
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in (
                "agent_bridge/src/agent_bridge/model_connections",
                "llm_providers/src/llm_providers/connections",
                "scripts/model-connections-smoke",
            )
            for p in (ROOT / folder).rglob("*.py")
        },
    }
    with args.output.open("x", encoding="utf-8") as output:
        try:
            for target in CASES if args.target == "all" else [args.target]:
                report["runs"].append(run(target))
        finally:
            json.dump(report, output, ensure_ascii=False, indent=2)
    passed = all(r["passed"] for r in report["runs"])
    print(json.dumps({"passed": passed, "output": str(args.output.resolve())}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""CPA Luna acceptance through the existing bounded synthetic smoke scenarios."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
import yaml
from llm_providers.connections import (
    ConnectionDefinition,
    CustomModel,
    ModelDirectory,
    model_reference,
)

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--effort", help="explicit custom OpenAI effort string")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=Path.home() / ".local/share/cliproxyapi/config.yaml"
    )
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("requires --allow-llm; normally nine generation requests, no retries")
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    key = config["api-keys"][0]
    base = "http://127.0.0.1:8317/v1"
    report = {
        "endpoint": base + "/responses",
        "model": "gpt-5.6-luna",
        "context_window_fixture": 100000,
        "scenarios": {},
    }
    responses = load("cpa_responses_smoke", ROOT / "scripts/responses-smoke/run.py")
    background = load("cpa_background_smoke", ROOT / "scripts/model-connections-smoke/run.py")
    report["source"] = responses.source_identity()
    import hashlib

    report["business_source_hashes"] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (
            "agent/src",
            "agent_bridge/src",
            "memory/src",
            "scripts/model-connections-smoke",
        )
        for path in (ROOT / folder).rglob("*.py")
    }
    with args.output.open("x", encoding="utf-8") as output:
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                result = client.get(base + "/models", headers={"Authorization": "Bearer " + key})
                result.raise_for_status()
                assert any(m.get("id") == "gpt-5.6-luna" for m in result.json()["data"])
            report["discovery"] = {"passed": True, "endpoint": base + "/models"}
            connection = ConnectionDefinition(
                str(UUID(int=121)),
                "CPA Luna smoke",
                base,
                "responses",
                (CustomModel(str(UUID(int=122)), "gpt-5.6-luna", 100000),),
            )
            directory = ModelDirectory([connection])
            selected = directory.default_config(
                model_reference(connection.id, connection.models[0].id)
            )
            if args.effort is not None:
                from dataclasses import replace

                selected = replace(selected, reasoning_preset="effort:" + args.effort)
            report["reasoning_preset"] = selected.reasoning_preset
            spec = directory.build_spec(
                selected,
                api_key=key,
            )
            for scenario in ("nonstream-json", "tool-loop"):
                report["scenarios"][scenario] = responses.run_provider(
                    "kimi", scenario, spec_override=spec
                )
                if not report["scenarios"][scenario]["passed"]:
                    raise RuntimeError("CPA scenario failed; remaining calls skipped")
            budget = responses.WireBudget("127.0.0.1", "/v1/responses", "http", 8317, limit=10)
            send = httpx.AsyncHTTPTransport.handle_async_request
            request = background.ObservedModel._request

            async def guarded_send(transport, outgoing):
                budget.observe(outgoing)
                return await send(transport, outgoing)

            def observed_request(model, value):
                budget.call_requests = 0
                return request(model, value)

            with (
                tempfile.TemporaryDirectory(prefix="af121-cpa-") as temp,
                patch.object(httpx.AsyncHTTPTransport, "handle_async_request", guarded_send),
                patch.object(background.ObservedModel, "_request", observed_request),
            ):
                report["scenarios"]["background"] = background.run(
                    "cpa-responses", Path(temp), spec_override=spec
                )
            report["background_wire_requests"] = budget.requests
        except BaseException as error:
            report["error"] = {"type": type(error).__name__}
        finally:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    passed = (
        "error" not in report
        and len(report["scenarios"]) == 3
        and all(r["passed"] for r in report["scenarios"].values())
    )
    print(json.dumps({"passed": passed, "output": str(args.output.resolve())}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

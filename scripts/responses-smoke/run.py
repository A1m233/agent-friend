"""Bounded, synthetic LanguageModel tool-loop smoke; never enabled by check."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from llm_providers.responses.stream import ResponsesState

from llm_providers import LLMTextDelta, LLMTurnDone, ModelRequest, create_language_model
from runtime_config import credential_source_for_mode

ROOT = Path(__file__).resolve().parents[2]
ROUTES = {
    "deepseek": (
        "deepseek/deepseek-v4-flash",
        "DEEPSEEK_API_KEY",
        "api.deepseek.com",
        "/responses",
        None,
    ),
    "kimi": ("kimi/kimi-k3", "KIMI_API_KEY", "api.moonshot.cn", "/v1/responses", "low"),
}
TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_fixture",
        "description": "Look up the current value of a synthetic test record.",
        "parameters": {
            "type": "object",
            "properties": {"record": {"type": "string"}},
            "required": ["record"],
            "additionalProperties": False,
        },
    },
}


class SmokeBoundaryExceeded(BaseException):
    """Bypass production retry handling before an unauthorized HTTP request."""


class WireBudget:
    def __init__(self, host: str, path: str) -> None:
        self.host, self.path = host, path
        self.requests: list[dict[str, Any]] = []
        self.call_requests = 0

    def observe(self, request: httpx.Request) -> None:
        if (
            request.method != "POST"
            or request.url.scheme != "https"
            or request.url.host != self.host
            or request.url.path != self.path
            or self.call_requests >= 1
            or len(self.requests) >= 2
        ):
            raise SmokeBoundaryExceeded("unexpected endpoint or request budget exceeded")
        self.call_requests += 1
        self.requests.append(
            {
                "method": request.method,
                "host": request.url.host,
                "path": request.url.path,
                "body_sha256": hashlib.sha256(request.content).hexdigest(),
            }
        )


def source_identity() -> dict[str, Any]:
    files = [Path(__file__), ROOT / "uv.lock", *sorted((ROOT / "llm_providers/src").rglob("*.py"))]
    hashes = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files_sha256": hashes,
        "source_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
    }


def safe_error(error: BaseException, secret: str) -> dict[str, Any]:
    result: dict[str, Any] = {"type": type(error).__name__}
    # Only synthetic input is sent. Retain the provider's useful error message,
    # not a raw exception repr containing request headers/body or reasoning.
    current: BaseException | None = error
    for _ in range(8):
        if current is None:
            break
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            payload = body.get("error", body)
            if isinstance(payload, dict):
                for key in ("code", "type", "message"):
                    if value := payload.get(key):
                        result[f"provider_{key}"] = str(value).replace(secret, "[REDACTED]")[:1000]
        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            result["status_code"] = status
        current = current.__cause__
    return result


def run_provider(provider: str) -> dict[str, Any]:
    model_id, key_name, host, path, preset = ROUTES[provider]
    credential = credential_source_for_mode("desktop").get(key_name)
    report: dict[str, Any] = {
        "model": model_id,
        "reasoning_preset": preset or "default",
        "calls": [],
        "passed": False,
    }
    if not credential.value:
        report["error"] = {"type": "MissingCredential", "credential": key_name}
        return report
    model = create_language_model(
        provider_id=provider,
        model_id=model_id,
        api_key=credential.value,
        reasoning_preset=preset,
        timeout_seconds=60,
    )
    budget = WireBudget(host, path)
    send = httpx.AsyncHTTPTransport.handle_async_request
    finish = ResponsesState.finish
    terminal_usage: dict[str, Any] = {}

    def observe_terminal(state: ResponsesState, diagnostics: Any) -> LLMTurnDone:
        # Read-only instrumentation of native counters, not a replacement for
        # the public LanguageModel call or normalized output assertions.
        native_usage = (state.terminal or {}).get("usage") or {}
        terminal_usage["reasoning_tokens"] = (native_usage.get("output_tokens_details") or {}).get(
            "reasoning_tokens"
        )
        return finish(state, diagnostics)

    async def guarded_send(
        transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        budget.observe(request)
        return await send(transport, request)

    def block_sync(*_: Any, **__: Any) -> Any:
        raise SmokeBoundaryExceeded("unexpected synchronous transport")

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": "Use lookup_fixture to retrieve record AF-114, then tell me its value. Do not guess the value.",
        }
    ]
    try:
        with (
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", guarded_send),
            patch.object(httpx.HTTPTransport, "handle_request", block_sync),
            patch.object(ResponsesState, "finish", observe_terminal),
        ):
            for turn in range(2):
                budget.call_requests = 0
                kinds: Counter[str] = Counter()
                phases: Counter[str] = Counter()
                text = ""
                done: LLMTurnDone | None = None
                call: dict[str, Any] = {
                    "turn": turn + 1,
                    "event_counts": kinds,
                    "phase_counts": phases,
                }
                report["calls"].append(call)
                for event in model.stream(
                    ModelRequest(
                        messages=messages,
                        tools=[TOOL],
                        settings={"max_tokens": 1024, "tool_choice": "auto"},
                    )
                ):
                    kinds[event.type] += 1
                    if isinstance(event, LLMTextDelta):
                        phases[event.phase or "none"] += 1
                        text += event.text
                    elif isinstance(event, LLMTurnDone):
                        if done is not None:
                            raise AssertionError("multiple terminal events")
                        done = event
                call["text_chars"] = len(text)
                if done is None or done.assistant_turn is None:
                    raise AssertionError("missing completed assistant turn")
                call["stop_reason"] = done.stop_reason
                call["usage"] = asdict(done.usage) if done.usage else None
                call["native_reasoning_tokens"] = terminal_usage.get("reasoning_tokens")
                call["reasoning_state_present"] = done.reasoning_state is not None
                if provider == "deepseek" and (
                    terminal_usage.get("reasoning_tokens") != 0 or done.reasoning_state is not None
                ):
                    raise AssertionError(
                        "default thinking-off was not verified by native usage/state"
                    )
                if done.diagnostics:
                    call["transport"] = done.diagnostics.transport
                    call["request_fingerprint"] = done.diagnostics.request_fingerprint
                    call["response_model"] = done.diagnostics.response_model
                if turn == 0:
                    calls = done.assistant_turn.tool_calls
                    if done.stop_reason != "tool_use" or len(calls) != 1:
                        raise AssertionError("expected one completed function call")
                    tool_call = calls[0]
                    if tool_call.tool_name != "lookup_fixture" or json.loads(
                        tool_call.arguments_json
                    ) != {"record": "AF-114"}:
                        raise AssertionError("incorrect function or arguments")
                    messages.append(dict(done.assistant_turn.to_openai_message()))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.tool_call_id,
                            "content": '{"value":"cobalt-73"}',
                        }
                    )
                elif (
                    done.stop_reason != "end_turn"
                    or done.assistant_turn.tool_calls
                    or "cobalt-73" not in text
                ):
                    raise AssertionError("tool result was not consumed into a final answer")
        report["passed"] = True
    except (Exception, SmokeBoundaryExceeded) as error:
        report["error"] = safe_error(error, credential.value)
        if isinstance(error, AssertionError):
            report["error"]["assertion"] = str(error)
    finally:
        report["wire_requests"] = budget.requests
        report["real_requests"] = len(budget.requests)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--provider", choices=["deepseek", "kimi", "both"], default="both")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error(
            "requires explicit authorization and --allow-llm; up to two requests per provider"
        )
    # Reserve output before any paid calls; never silently overwrite old evidence.
    with args.output.open("x", encoding="utf-8") as output:
        report: dict[str, Any] = {
            "started_at": datetime.now(UTC).isoformat(),
            "source": source_identity(),
            "providers": [],
        }
        try:
            for provider in ROUTES if args.provider == "both" else [args.provider]:
                report["providers"].append(run_provider(provider))
        finally:
            report["finished_at"] = datetime.now(UTC).isoformat()
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "passed": all(item["passed"] for item in report["providers"]),
            }
        )
    )
    return 0 if all(item["passed"] for item in report["providers"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

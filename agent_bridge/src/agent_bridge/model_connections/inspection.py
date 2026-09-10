"""Ephemeral, owner-authorized draft inspection; no settings or credential writes."""

import json
import time
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import httpx
from llm_providers.connections import (
    ConnectionDefinition,
    CustomModel,
    ModelDirectory,
    model_reference,
)
from llm_providers.connections.metadata import discovered_models, model_metadata
from llm_providers.errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMModelUnavailableError,
    LLMNetworkError,
    LLMPaymentRequiredError,
    LLMPermissionError,
    LLMRateLimitError,
)

from llm_providers import LLMClient, LLMTurnDone, ModelCallControl, ModelRequest
from runtime_config import require_secret

from .runtime import ConnectionRuntime


def error_code(error: Exception) -> str:
    if isinstance(error, (LLMAuthError,)):
        return "authentication_failed"
    if isinstance(error, LLMPermissionError):
        return "permission_denied"
    if isinstance(error, LLMPaymentRequiredError):
        return "billing_required"
    if isinstance(error, LLMRateLimitError):
        return "rate_limited"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "request_timeout"
    if isinstance(error, (LLMNetworkError, httpx.RequestError)):
        return "network_unavailable"
    if isinstance(error, LLMModelUnavailableError):
        return "model_unavailable"
    if isinstance(error, LLMBadRequestError):
        return "protocol_or_model_rejected"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return {
            401: "authentication_failed",
            403: "permission_denied",
            404: "model_list_unavailable",
            429: "rate_limited",
        }.get(status, "upstream_unavailable")
    if isinstance(error, ValueError):
        return "invalid_response"
    return "upstream_unavailable"


def inspect_draft(body: dict[str, Any], runtime: ConnectionRuntime) -> dict[str, Any]:
    mode = body.get("mode")
    name = body.get("model", "connection-probe")
    if not isinstance(name, str) or not name.strip() or len(name) > 512:
        raise ValueError("invalid_model_name")
    # A probe's small request budget is not a discovered model context limit.
    model = CustomModel(str(UUID(int=2)), name.strip(), 8192)
    if mode == "metadata":
        return {"metadata": model_metadata(model.name)}
    if mode not in {"discover", "probe"}:
        raise ValueError("invalid_inspection_mode")
    base, protocol = body.get("baseUrl"), body.get("protocol")
    if not isinstance(base, str) or not isinstance(protocol, str):
        raise ValueError("invalid_connection_configuration")
    connection = ConnectionDefinition(
        str(UUID(int=1)), "Draft inspection", base.strip(), cast(Any, protocol), (model,)
    )
    key = body.get("apiKey", "")
    if not isinstance(key, str):
        raise ValueError("api_key_required")
    if not key.strip():
        existing = runtime.directory().connections.get(body.get("connectionId", ""))
        if existing is None:
            raise ValueError("api_key_required")
        key = require_secret(runtime.source, existing.credential_name)
    if mode == "discover":
        try:
            with (
                httpx.Client(timeout=15, follow_redirects=False) as client,
                client.stream(
                    "GET",
                    connection.base_url.rstrip("/") + "/models",
                    headers={"Authorization": "Bearer " + key.strip()},
                ) as response,
            ):
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 2_000_000:
                        raise ValueError("model_list_too_large")
            return {"models": discovered_models(json.loads(content))}
        except Exception as error:
            return {"error": error_code(error)}
    return probe(connection, key.strip())


def probe(connection: ConnectionDefinition, key: str) -> dict[str, Any]:
    directory = ModelDirectory([connection])
    selection = directory.default_config(model_reference(connection.id, connection.models[0].id))
    spec = directory.build_spec(selection, api_key=key)
    client = LLMClient(
        replace(spec, defaults={**spec.defaults, "timeout": 25}),
        network_retries=0,
        engine_overload_retries=0,
    )
    deadline = time.monotonic() + 60

    def check() -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError()

    control = ModelCallControl(cancel_check=check, allow_retries=False)
    result: dict[str, Any] = {
        "metadata": model_metadata(connection.models[0].name),
        "generation": {"status": "pending"},
        "tools": {"status": "skipped"},
    }
    try:
        response = client.generate(
            ModelRequest(
                messages=[{"role": "user", "content": "Reply only OK."}],
                settings={"max_tokens": 4096},
                control=control,
            )
        )
        if not response.text.strip():
            raise ValueError("empty_response")
        result["generation"] = {"status": "passed"}
    except Exception as error:
        result["generation"] = {"status": "failed", "code": error_code(error)}
        return result
    tool = {
        "type": "function",
        "function": {
            "name": "connection_check",
            "description": "Return a synthetic connection test marker. No external effects.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": "Call connection_check once, then report the marker returned by the tool.",
        }
    ]
    try:
        done = None
        stream = client.stream(
            ModelRequest(
                messages=messages,
                tools=[tool],
                settings={
                    "max_tokens": 4096,
                },
                control=control,
            )
        )
        try:
            for event in stream:
                if isinstance(event, LLMTurnDone):
                    done = event
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if (
            done is None
            or done.stop_reason != "tool_use"
            or done.assistant_turn is None
            or len(done.assistant_turn.tool_calls) != 1
        ):
            raise ValueError("tool_call_missing")
        call = done.assistant_turn.tool_calls[0]
        if call.tool_name != "connection_check" or json.loads(call.arguments_json) != {}:
            raise ValueError("unexpected_tool_call")
        messages.extend(dict(item) for item in done.assistant_turn.to_openai_messages())
        messages.append(
            {"role": "tool", "tool_call_id": call.tool_call_id, "content": "connection-ok-121"}
        )
        response = client.generate(
            ModelRequest(
                messages=messages,
                tools=[tool],
                settings={"max_tokens": 4096},
                control=control,
            )
        )
        if "connection-ok-121" not in response.text:
            raise ValueError("tool_result_not_consumed")
        result["tools"] = {"status": "passed"}
    except Exception as error:
        result["tools"] = {"status": "failed", "code": error_code(error)}
    return result

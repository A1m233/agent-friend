"""One-off authorized K3 phase observation: <=4 raw Responses POSTs, no retries."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import httpx
from llm_providers import build_provider_spec
from llm_providers.responses.stream import ResponsesState
from runtime_config import credential_source_for_mode

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://api.moonshot.cn/v1/responses"
PHASE_PROMPT = (ROOT / "agent/src/agent/prompt_sections/assistant_phase.md").read_text(encoding="utf-8")
TOOL = {
    "type": "function", "name": "lookup_fixture",
    "description": "Look up the value of a synthetic test record. Use this tool rather than guessing.",
    "parameters": {"type": "object", "properties": {"record": {"type": "string"}},
                   "required": ["record"], "additionalProperties": False},
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def phase_shape(item: dict) -> dict:
    return {
        "id": item.get("id"), "type": item.get("type"), "role": item.get("role"),
        "status": item.get("status"), "phase_present": "phase" in item,
        "phase": item.get("phase"),
        "content_types": [p.get("type") for p in item.get("content", []) if isinstance(p, dict)],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-llm", action="store_true")
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    if not args.allow_llm:
        ap.error("explicit real-model authorization required")
    source_files = [Path(__file__), ROOT / "uv.lock", ROOT / "llm_providers/src/llm_providers/catalog.py",
                    ROOT / "llm_providers/src/llm_providers/responses/stream.py",
                    ROOT / "agent/src/agent/prompt_sections/assistant_phase.md"]
    hashes = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p.read_bytes()) for p in source_files}
    report = {
        "started_at": datetime.now(UTC).isoformat(), "endpoint": ENDPOINT,
        "model": "kimi-k3", "max_requests": 4, "max_output_tokens_per_request": 1024,
        "automatic_retries": 0, "synthetic_only": True, "raw_sse_json_before_sdk": True,
        "raw_reasoning_retained": False, "phase_prompt": PHASE_PROMPT,
        "source": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   "files_sha256": hashes, "python": platform.python_version(),
                   "httpx": version("httpx"), "openai": version("openai")},
        "calls": [],
    }
    # Reserve a unique artifact before retrieving credentials or sending requests.
    with args.output.open("x", encoding="utf-8") as out:
        try:
            credential = credential_source_for_mode("desktop").get("KIMI_API_KEY")
            if not credential or not credential.value:
                raise RuntimeError("credential_unavailable")
            secret = credential.value
            spec = build_provider_spec(provider_id="kimi", model_id="kimi/kimi-k3", api_key=secret)
            with httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=False,
                              transport=httpx.HTTPTransport(retries=0)) as client:

                def request(label: str, inputs: list[dict], effort: str, tools: bool = False) -> dict:
                    if len(report["calls"]) >= 4:
                        raise RuntimeError("request_budget_exceeded")
                    body = {"model": "kimi-k3", "input": inputs, "stream": True,
                            "reasoning": {"effort": effort}, "max_output_tokens": 1024}
                    if tools:
                        body.update(tools=[TOOL], tool_choice="auto")
                    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
                    call = {"scenario": label, "reasoning_effort": effort,
                            "started_at": datetime.now(UTC).isoformat(),
                            "request_body_sha256": sha(encoded), "request_bytes": len(encoded),
                            "input_roles_or_types": [i.get("role", i.get("type")) for i in inputs],
                            "events": Counter(), "message_lifecycle": [], "parser_errors": [],
                            "parser_item_phases": [], "terminal_status": None}
                    report["calls"].append(call)
                    state = ResponsesState(spec)
                    terminal = {}
                    started = time.monotonic()
                    total_bytes = 0
                    data_lines = []
                    try:
                        with client.stream("POST", ENDPOINT, content=encoded,
                                           headers={"Authorization": "Bearer " + secret,
                                                    "Content-Type": "application/json"}) as response:
                            call["http_status"] = response.status_code
                            if response.status_code != 200:
                                call["error"] = {"type": "http_rejected"}
                                return {}
                            if "text/event-stream" not in response.headers.get("content-type", ""):
                                raise RuntimeError("unexpected_content_type")

                            def consume(data: str) -> None:
                                nonlocal terminal
                                if data == "[DONE]":
                                    return
                                raw = json.loads(data)
                                kind = raw.get("type", "<missing>")
                                call["events"][kind] += 1
                                item = raw.get("item")
                                if isinstance(item, dict) and item.get("type") == "message":
                                    call["message_lifecycle"].append({"event": kind, **phase_shape(item)})
                                if kind in {"response.completed", "response.incomplete", "response.failed"}:
                                    terminal = raw.get("response") or {}
                                    call["terminal_status"] = terminal.get("status")
                                    call["response_model"] = terminal.get("model")
                                    call["usage"] = terminal.get("usage")
                                    for item in terminal.get("output", []):
                                        if item.get("type") == "message":
                                            call["message_lifecycle"].append({"event": kind, **phase_shape(item)})
                                    call["output_item_types"] = [i.get("type") for i in terminal.get("output", [])]
                                try:
                                    state.feed(raw)
                                except Exception as exc:
                                    call["parser_errors"].append({"event": kind, "type": type(exc).__name__})

                            for line in response.iter_lines():
                                total_bytes += len(line.encode())
                                if total_bytes > 1_000_000 or time.monotonic() - started > 90:
                                    raise RuntimeError("response_boundary_exceeded")
                                if not line:
                                    if data_lines:
                                        consume("\n".join(data_lines))
                                        data_lines.clear()
                                elif line.startswith("data:"):
                                    data_lines.append(line[5:].lstrip())
                            if data_lines:
                                consume("\n".join(data_lines))
                        call["parser_item_phases"] = [
                            {"id": i.identity, "phase": i.phase} for i in state.items.values() if i.kind == "message"
                        ]
                        text = "".join(p.get("text", "") for i in terminal.get("output", [])
                                       if i.get("type") == "message" for p in i.get("content", [])
                                       if p.get("type") == "output_text")
                        call["visible_text_chars"] = len(text)
                        call["expected_result_present"] = ("cobalt-73" in text if label == "tool_continuation_low"
                                                           else "391" in text if label.startswith("direct_") else None)
                    except Exception as exc:
                        call["error"] = {"type": type(exc).__name__}
                    finally:
                        call["elapsed_seconds"] = round(time.monotonic() - started, 3)
                        call["response_bytes"] = total_bytes
                        print(json.dumps({k: call.get(k) for k in ("scenario", "http_status", "terminal_status", "message_lifecycle", "parser_errors", "elapsed_seconds")}, ensure_ascii=False), flush=True)
                    return terminal

                request("direct_low", [{"role": "user", "content": "What is 17 times 23? Reply with only the number."}], "low")
                tool_inputs = [
                    {"role": "system", "content": PHASE_PROMPT},
                    {"role": "user", "content": "请先用一句话说明接下来要做什么，再调用 lookup_fixture 查询 AF-K3-PHASE 记录。工具返回后给出最终查询值。不要猜测，不要输出隐藏推理。"},
                ]
                first = request("tool_with_phase_prompt_low", tool_inputs, "low", tools=True)
                calls = [i for i in first.get("output", []) if i.get("type") == "function_call"]
                if (first.get("status") == "completed" and len(calls) == 1
                        and calls[0].get("name") == "lookup_fixture"
                        and json.loads(calls[0].get("arguments", "{}")) == {"record": "AF-K3-PHASE"}):
                    # Native output replay stays in memory; hidden reasoning is never saved in the report.
                    continuation = tool_inputs + first["output"] + [
                        {"type": "function_call_output", "call_id": calls[0]["call_id"],
                         "output": '{"value":"cobalt-73"}'},
                    ]
                    request("tool_continuation_low", continuation, "low", tools=True)
                else:
                    report["continuation_skipped"] = "expected_single_complete_tool_call_not_observed"
                request("direct_high_with_phase_prompt", [
                    {"role": "system", "content": PHASE_PROMPT},
                    {"role": "user", "content": "17乘23是多少？只回答最终数字。"},
                ], "high")
        except Exception as exc:
            report["error"] = {"type": type(exc).__name__}
        finally:
            report["finished_at"] = datetime.now(UTC).isoformat()
            report["real_requests"] = len(report["calls"])
            report["reported_total_tokens"] = sum((c.get("usage") or {}).get("total_tokens", 0) for c in report["calls"])
            report["source_unchanged"] = all(sha((ROOT / p).read_bytes()) == digest for p, digest in hashes.items())
            json.dump(report, out, ensure_ascii=False, indent=2)
            out.write("\n")
    print(json.dumps({"output": str(args.output.resolve()), "real_requests": report["real_requests"],
                      "reported_total_tokens": report["reported_total_tokens"]}), flush=True)
    return 0 if len(report["calls"]) == 4 and all(c.get("terminal_status") == "completed" and not c.get("error") for c in report["calls"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

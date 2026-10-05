"""115: bounded synthetic Vision -> Agent -> durable phase smoke, opt-in only."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import httpx
from agent.sessions import restore_rollout
from llm_providers.responses.stream import ResponsesState
from PIL import Image, ImageDraw, ImageFont

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    AttachmentRef,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    SessionManager,
    TextDelta,
    ToolRegistry,
    ToolResult,
)
from llm_providers import (
    LLMClient,
    LLMStreamEvent,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    MultimodalInputAdapter,
    build_provider_spec,
    get_model_capabilities,
)
from runtime_config import credential_source_for_mode

ROOT = Path(__file__).resolve().parents[2]
MODEL = "deepseek/deepseek-flash"
IMAGE_CODE = "AF-115"


class BoundaryExceeded(BaseException):
    """Bypasses SDK/Agent retries, before an unauthorized request leaves the host."""


class WireBudget:
    def __init__(self) -> None:
        self.uploads = 0
        self.generations = 0
        self.current_call = 0
        self.sent_calls: set[int] = set()
        self.requests: list[dict[str, Any]] = []

    def observe(self, request: httpx.Request) -> None:
        if (
            request.url.scheme != "https"
            or request.url.host != "api.deepseek.com"
            or request.method != "POST"
        ):
            raise BoundaryExceeded("unexpected destination/method")
        if request.url.path == "/files":
            raise BoundaryExceeded("native attachment upload is no longer supported")
        elif request.url.path == "/responses":
            body = json.loads(request.content)
            if (
                body.get("model") != MODEL.split("/", 1)[1]
                or not isinstance(body.get("max_output_tokens"), int)
                or not 0 < body["max_output_tokens"] <= 1024
                or self.generations >= 2
                or self.current_call < 1
                or self.current_call in self.sent_calls
            ):
                raise BoundaryExceeded("generation/retry/model/token boundary exceeded")
            self.generations += 1
            self.sent_calls.add(self.current_call)
        else:
            raise BoundaryExceeded("unexpected endpoint")
        self.requests.append({"path": request.url.path, "method": request.method})


class ObservedModel:
    def __init__(self, client: LLMClient, budget: WireBudget) -> None:
        self.client, self.budget = client, budget
        self.calls: list[dict[str, Any]] = []
        self.context_window = client.context_window
        self.spec = client.spec
        self.capabilities = get_model_capabilities(client)

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise BoundaryExceeded("generate is outside this smoke")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        trace = request.control.trace_context if request.control else None
        if len(self.calls) >= 2 or (trace is not None and trace.recovery_attempt):
            raise BoundaryExceeded("only two non-retry stream calls are authorized")
        self.budget.current_call += 1
        observation: dict[str, Any] = {"index": self.budget.current_call}
        self.calls.append(observation)
        request = replace(request, settings={**request.settings, "max_tokens": 1024})
        for event in self.client.stream(request):
            if isinstance(event, LLMTurnDone):
                observation.update(
                    {
                        "stop_reason": event.stop_reason,
                        "usage": asdict(event.usage) if event.usage else None,
                        "reasoning_state_present": event.reasoning_state is not None,
                        "phases": [item.phase for item in event.assistant_turn.text_items]
                        if event.assistant_turn
                        else [],
                        "tool_count": len(event.assistant_turn.tool_calls)
                        if event.assistant_turn
                        else 0,
                        "transport": event.diagnostics.transport if event.diagnostics else None,
                        "request_fingerprint": event.diagnostics.request_fingerprint
                        if event.diagnostics
                        else None,
                    }
                )
            yield event


class LookupFixture:
    name: ClassVar[str] = "lookup_fixture"
    description: ClassVar[str] = (
        "Return the synthetic test image and its associated secret test value. Read this tool output to answer the image task."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, image: AttachmentRef) -> None:
        self.image = image
        self.calls = 0

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        return ToolResult(text='{"value":"cobalt-115"}', images=(self.image,))


def source_identity() -> dict[str, Any]:
    paths = [ROOT / "uv.lock", Path(__file__)]
    for package in ("agent", "agent_bridge", "llm_providers"):
        paths.extend(
            path for path in (ROOT / package / "src").rglob("*") if path.suffix in {".py", ".md"}
        )
    hashes = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files_sha256": hashes,
    }


def safe_error_chain(error: BaseException, secret: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    current: BaseException | None = error
    for _ in range(8):
        if current is None:
            break
        item: dict[str, Any] = {"type": type(current).__name__}
        message = str(current)
        if message.startswith(("responses_protocol_error:", "attachment_")):
            item["message"] = message.replace(secret, "[REDACTED]")[:1000]
        diagnostics = getattr(current, "diagnostics", None)
        if diagnostics is not None:
            item["diagnostics"] = {
                key: getattr(diagnostics, key, None)
                for key in (
                    "transport",
                    "response_id",
                    "provider_request_id",
                    "request_fingerprint",
                )
            }
        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            item["status_code"] = status
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            payload = body.get("error", body)
            if isinstance(payload, dict):
                for key in ("code", "type", "message"):
                    if isinstance(payload.get(key), str):
                        item[f"provider_{key}"] = payload[key].replace(secret, "[REDACTED]")[:1000]
        result.append(item)
        current = current.__cause__
    return result


def event_shape(event: Any) -> dict[str, Any]:
    raw = event if isinstance(event, dict) else event.model_dump(exclude_none=True)
    shape = {key: raw[key] for key in ("type", "output_index", "content_index") if key in raw}
    item = raw.get("item")
    if isinstance(item, dict):
        content = item.get("content")
        shape["item"] = {
            "type": item.get("type"),
            "status": item.get("status"),
            "phase": item.get("phase")
            if item.get("phase") in ("commentary", "final_answer")
            else "missing"
            if "phase" not in item
            else "unknown",
            "content_types": [
                part.get("type")
                for part in (content if isinstance(content, list) else [])
                if isinstance(part, dict)
            ],
        }
    response = raw.get("response")
    if isinstance(response, dict):
        output = response.get("output")
        shape["response_status"] = response.get("status")
        shape["output_types"] = [
            part.get("type")
            for part in (output if isinstance(output, list) else [])
            if isinstance(part, dict)
        ]
    return shape


def run() -> dict[str, Any]:
    report: dict[str, Any] = {"model": MODEL, "reasoning_preset": "default", "passed": False}
    budget = WireBudget()
    credential = credential_source_for_mode("desktop").get("DEEPSEEK_API_KEY")
    if credential is None or not credential.value:
        return {**report, "error": "credential_unavailable", "wire_requests": []}
    client = LLMClient(
        build_provider_spec(provider_id="deepseek", model_id=MODEL, api_key=credential.value),
        network_retries=0,
        engine_overload_retries=0,
    )
    model = ObservedModel(client, budget)
    stream_shapes: list[dict[str, Any]] = []
    original_feed = ResponsesState.feed
    original_sync = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request

    def observed_feed(state: ResponsesState, event: Any) -> Any:
        shape = event_shape(event)
        if not str(shape.get("type", "")).endswith(".delta") and len(stream_shapes) < 256:
            stream_shapes.append(shape)
        return original_feed(state, event)

    def guarded_sync(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        budget.observe(request)
        response = original_sync(transport, request)
        budget.requests[-1]["status_code"] = response.status_code
        return response

    async def guarded_async(
        transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        budget.observe(request)
        response = await original_async(transport, request)
        budget.requests[-1]["status_code"] = response.status_code
        return response

    try:
        with (
            tempfile.TemporaryDirectory(prefix="af-phase-115-") as temporary,
            patch.object(httpx.HTTPTransport, "handle_request", guarded_sync),
            patch.object(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async),
            patch.object(ResponsesState, "feed", observed_feed),
        ):
            root = Path(temporary)
            path = root / "synthetic.png"
            picture = Image.new("RGB", (640, 240), "white")
            ImageDraw.Draw(picture).text(
                (100, 70), IMAGE_CODE, fill="black", font=ImageFont.load_default(size=72)
            )
            picture.save(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            report["image_sha256"] = digest
            attachment = AttachmentRef(
                asset_id=digest,
                display_name=path.name,
                kind="image",
                media_type="image/png",
                size_bytes=path.stat().st_size,
                sha256=digest,
            )
            adapter = MultimodalInputAdapter(open_asset=lambda _: path.open("rb"))
            catalog = PersonaCatalog(external_dir=root / "personas")
            tool = LookupFixture(attachment)
            registry = ToolRegistry([tool])
            store = JsonlSessionStore(root / "sessions")
            manager = SessionManager(
                store=store,
                llm_client_factory=lambda _: model,
                prompt_builder_factory=lambda pid: MarkdownPromptBuilder(
                    persona_id=pid, catalog=catalog
                ),
                context_manager_factory=NaiveContextManager,
                tool_registry=registry,
                message_renderer=lambda message, _: adapter.render_message(message, client),
            )
            session_id: str | None = None
            try:
                session = manager.create(
                    title="synthetic phase smoke",
                    persona="default",
                    persona_id=BUILTIN_DEFAULT_PERSONA_ID,
                    model=MODEL,
                )
                session_id = session.session_id
                conversation = manager.start_conversation(session)
                output = list(
                    conversation.stream(
                        "请先简短说明接下来要做什么，调用 lookup_fixture 获取图片和对应值，再读取图片中的代码。"
                        "工具返回后，简短告诉我查询值。只需要这一次工具查询，不要做其他工作。",
                        attachments=[attachment],
                        run_id="smoke-115",
                    )
                )
                saved = store.load(session.session_id)
                restored = restore_rollout(saved)
                phases = [
                    event.payload.get("phase")
                    for event in saved.active_events
                    if event.type == "assistant_message"
                ]
                report.update(
                    {
                        "phases": phases,
                        "tool_invocations": tool.calls,
                        "source_ids_match": {
                            event.message_id for event in output if isinstance(event, TextDelta)
                        }
                        == {
                            event.uuid
                            for event in saved.active_events
                            if event.type == "assistant_message"
                        },
                        "replayed_phases": [
                            item.meta.get("phase")
                            for item in restored.history.items
                            if item.role == "assistant" and item.content
                        ],
                        "validation": [
                            event.payload.get("validation")
                            for event in saved.active_events
                            if event.type == "assistant_phase"
                        ],
                        "answer_contains_tool_value": any(
                            "cobalt-115" in str(event.payload.get("content", ""))
                            for event in saved.active_events
                            if event.type == "assistant_message"
                        ),
                    }
                )
                report["passed"] = (
                    budget.generations == 2
                    and budget.uploads == 0
                    and tool.calls == 1
                    and "commentary" in phases
                    and "final_answer" in phases
                    and report["source_ids_match"]
                    and report["replayed_phases"] == phases
                    and report["validation"][-1] == "valid"
                    and report["answer_contains_tool_value"]
                )
            finally:
                if session_id is not None:
                    try:
                        report["product_event_shapes"] = [
                            {
                                "type": event.type,
                                "phase": event.payload.get("phase"),
                                "partial": event.payload.get("partial"),
                                "content_chars": len(str(event.payload.get("content", ""))),
                            }
                            for event in store.load(session_id).active_events
                            if event.type
                            in {"assistant_message", "tool_call_request", "tool_call_result"}
                        ]
                    except Exception as probe_error:
                        report["persistence_probe_error"] = type(probe_error).__name__
                manager.close()
    except (Exception, BoundaryExceeded) as error:
        # Do not serialize SDK exception repr, request bodies, headers or reasoning.
        report["error"] = type(error).__name__
        report["error_chain"] = safe_error_chain(error, credential.value)
        if isinstance(error, BoundaryExceeded):
            report["boundary"] = str(error)
    report.update(
        {"wire_requests": budget.requests, "calls": model.calls, "stream_shapes": stream_shapes}
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error(
            "requires explicit authorization: one upload, two Vision calls, <=1024 output tokens each"
        )
    with args.output.open("x", encoding="utf-8") as output:
        report = {"started_at": datetime.now(UTC).isoformat(), "source": source_identity(), **run()}
        report["finished_at"] = datetime.now(UTC).isoformat()
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({"passed": report["passed"], "output": str(args.output.resolve())}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

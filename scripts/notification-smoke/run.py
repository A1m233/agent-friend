"""Three explicitly authorized notification calls against an isolated real-memory snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from agent.memory_search import SessionHistorySource, SessionStoreSourceOrderProvider
from agent.notifications import NotificationFact, NotificationStore, fallback_text
from agent.personas import EffectivePersonaStore
from agent_bridge.notifications.generation import NotificationGenerator, memory_block
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from agent_bridge.settings import BridgeSettings
from memory.contracts import MemoryContext
from memory.search import MemorySearchService

from agent import BUILTIN_DEFAULT_PERSONA_ID, JsonlSessionStore, PersonaCatalog
from llm_providers import (
    LanguageModel,
    LLMStreamEvent,
    LLMTurnDone,
    ModelRequest,
    ModelRuntimeConfig,
)
from memory import Memory, build_memory
from runtime_config import DesktopSettingsReader, config_mode_from_env, credential_source_for_mode

ROOT = Path(__file__).resolve().parents[2]


class SmokeBoundaryExceeded(BaseException):
    pass


def private_output(output: Path) -> Path:
    """Keep private persona/memory evidence outside every Git checkout."""
    if not output.is_absolute():
        raise ValueError("--output must be an absolute path outside Git checkouts")
    resolved = output.resolve()
    if resolved.is_relative_to(ROOT) or any(
        (parent / ".git").exists() for parent in resolved.parents
    ):
        raise ValueError("notification evidence cannot be written inside a Git checkout")
    return resolved


class InputEvidence:
    """Private, exclusive artifacts captured before the corresponding request is sent."""

    def __init__(self, output: Path) -> None:
        output = private_output(output)
        self.directory = output.with_name(output.stem + ".inputs")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.secret: str | None = None

    def write(self, record: dict[str, Any], stage: str, payload: Any) -> None:
        redacted_fields = redacted_values = 0

        def redact(value: Any) -> Any:
            nonlocal redacted_fields, redacted_values
            if isinstance(value, dict):
                result: dict[str, Any] = {}
                for key, item in value.items():
                    if key.lower().replace("_", "").replace("-", "") in {
                        "apikey",
                        "authorization",
                        "headers",
                        "extraheaders",
                        "cookie",
                        "password",
                        "secret",
                        "clientsecret",
                        "accesstoken",
                        "refreshtoken",
                    }:
                        result[key] = "[REDACTED]"
                        redacted_fields += 1
                    else:
                        result[key] = redact(item)
                return result
            if isinstance(value, (list, tuple)):
                return [redact(item) for item in value]
            if isinstance(value, str) and self.secret and self.secret in value:
                redacted_values += value.count(self.secret)
                return value.replace(self.secret, "[REDACTED]")
            return value

        safe = redact(payload)
        encoded = (json.dumps(safe, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        # The case index is assigned by this harness, never by model text.
        path = self.directory / f"{record['caseIndex']:02d}.{stage}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        record.setdefault("inputEvidence", {})[stage] = {
            "file": str(path.relative_to(self.directory.parent)),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "redactedFields": redacted_fields,
            "redactedValues": redacted_values,
        }


class ForbiddenExtractor:
    context_window = 1

    def generate(self, _: ModelRequest) -> Any:
        raise SmokeBoundaryExceeded("memory extraction is forbidden during this smoke")

    def stream(self, _: ModelRequest) -> Iterator[LLMStreamEvent]:
        raise SmokeBoundaryExceeded("memory extraction is forbidden during this smoke")
        yield  # pragma: no cover


class WireBudget:
    """One physical model POST per fact; no redirects, retries or side requests."""

    def __init__(self, evidence: InputEvidence) -> None:
        self.total = 0
        self.current: dict[str, Any] | None = None
        self.endpoint: tuple[str, str] | None = None
        self.evidence = evidence

    def observe(self, request: httpx.Request) -> None:
        if self.current is None or self.endpoint is None:
            raise SmokeBoundaryExceeded("network request outside a notification call")
        if (
            request.method != "POST"
            or request.url.scheme != "https"
            or (request.url.host, request.url.path) != self.endpoint
            or self.total >= 3
            or self.current["wireRequests"] >= 1
        ):
            raise SmokeBoundaryExceeded("unexpected request or authorized budget exceeded")
        try:
            body = json.loads(request.content)
        except (ValueError, UnicodeError) as error:
            raise SmokeBoundaryExceeded("model request must have a JSON body") from error
        self.evidence.write(self.current, "wire", body)
        self.total += 1
        self.current["wireRequests"] += 1
        self.current["endpoint"] = {"host": request.url.host, "path": request.url.path}
        self.current["requestBytes"] = len(request.content)


class ObservedModel:
    def __init__(self, model: LanguageModel, record: dict[str, Any], budget: WireBudget) -> None:
        self.model, self.record, self.budget = model, record, budget

    @property
    def context_window(self) -> int:
        return self.model.context_window

    def generate(self, _: ModelRequest) -> Any:
        raise SmokeBoundaryExceeded("only the notification stream caller is authorized")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        if request.tools or request.control.allow_retries:
            raise SmokeBoundaryExceeded("tools or retries were enabled")
        self.record["logicalCalls"] += 1
        if self.record["logicalCalls"] > 1:
            raise SmokeBoundaryExceeded("more than one call for a fact")
        self.record["inputChars"] = sum(len(str(m.get("content", ""))) for m in request.messages)
        self.budget.evidence.write(
            self.record,
            "model-request",
            {
                "messages": request.messages,
                "tools": request.tools,
                "settings": dict(request.settings),
                "control": {
                    "allow_retries": request.control.allow_retries,
                    "cancel_check_present": request.control.cancel_check is not None,
                    "operation": (
                        request.control.trace_context.operation
                        if request.control.trace_context is not None
                        else None
                    ),
                },
            },
        )
        for event in self.model.stream(request):
            if isinstance(event, LLMTurnDone):
                self.record["stopReason"] = event.stop_reason
                self.record["usage"] = asdict(event.usage) if event.usage else None
                self.record["responseModel"] = (
                    event.diagnostics.response_model if event.diagnostics else None
                )
            yield event


class ReadOnlyMemory:
    """Expose only production retrieve; all mutation attempts fail closed."""

    def __init__(self, inner: Memory, budget: WireBudget) -> None:
        self.inner, self.budget = inner, budget

    def retrieve(self, query: str, **kwargs: Any) -> MemoryContext:
        started = time.monotonic()
        result = self.inner.retrieve(query, **kwargs)
        if self.budget.current is not None:
            _, filtered, injected = memory_block(result)
            self.budget.current["recall"] = {
                "elapsedMs": round((time.monotonic() - started) * 1000, 2),
                "returnedItems": len(result.items),
                "filteredNotifications": filtered,
                "injectedItems": injected,
            }
        return result


def sqlite_snapshot(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"required real memory database is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original:
        original.execute("PRAGMA query_only=ON")
        with sqlite3.connect(destination) as snapshot:
            original.backup(snapshot)
    return {
        "source": str(source.resolve()),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def persona_snapshot(settings: BridgeSettings, temporary: Path) -> EffectivePersonaStore:
    source_file = settings.personas_dir / "default" / "persona.md"
    source_internal = settings.personas_dir / ".internal" / "default"
    destination = temporary / "persona"
    destination.mkdir()
    if source_file.is_file():
        shutil.copyfile(source_file, destination / "persona.md")
    internal = destination / "internal"
    internal.mkdir()
    for name in ("state.json", "last-valid.md", "undo.md"):
        if (source_internal / name).is_file():
            shutil.copyfile(source_internal / name, internal / name)
    catalog = PersonaCatalog(external_dir=settings.personas_dir)
    return EffectivePersonaStore(
        user_file=destination / "persona.md",
        internal_dir=internal,
        builtin_loader=lambda: catalog.read_content(BUILTIN_DEFAULT_PERSONA_ID),
    )


def facts() -> tuple[NotificationFact, ...]:
    common = {"persona_id": BUILTIN_DEFAULT_PERSONA_ID, "created_at": datetime.now(UTC)}
    return (
        NotificationFact(
            event_key="smoke:reply",
            kind="reply_ready",
            title="整理照片",
            task="帮我设计一下照片文件夹的整理方式。",
            reply="整理方式已经写好了。你希望按拍摄年份还是按旅行目的地来分类？",
            **common,
        ),
        NotificationFact(
            event_key="smoke:approval",
            kind="needs_action",
            title="整理项目文件",
            task="检查项目文件并整理重复内容。",
            detail="需要用户确认一次文件访问操作。",
            **common,
        ),
        NotificationFact(
            event_key="smoke:todo",
            kind="todo_due",
            title="起身活动一下",
            detail="已经到约定时间，起来走走、喝点水。",
            **common,
        ),
    )


def run(output: Path, *, allow_llm: bool) -> dict[str, Any]:
    output = private_output(output)
    settings = BridgeSettings()
    reader = DesktopSettingsReader()
    report: dict[str, Any] = {
        "startedAt": datetime.now(UTC).isoformat(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "settingsPath": str(reader.path),
        "settingsChatModel": (reader.read() or {}).get("chatModel"),
        "personaPath": str(settings.personas_dir / "default" / "persona.md"),
        "memoryMode": "readonly-source-sqlite-backup",
        "inputEvidenceDirectory": str(output.with_name(output.stem + ".inputs")),
        "inputEvidencePrivacy": "private persona/memory; no headers; credential values redacted",
        "limit": 3,
        "cases": [],
        "realMemoryWrites": 0,
        "sessionWrites": 0,
        "imSends": 0,
        "realNotificationWrites": 0,
    }
    with tempfile.TemporaryDirectory(prefix="agent-friend-notification-smoke-") as temporary:
        temp = Path(temporary)
        persona = persona_snapshot(settings, temp)
        effective = persona.load()
        report["personaRevision"] = effective.revision
        report["personaSource"] = effective.source
        report["personaChars"] = len(effective.content)
        if not allow_llm:
            report["mode"] = "preflight-only"
            report["memoryExists"] = settings.memory_db.is_file()
            return report
        evidence = InputEvidence(output)
        snapshot_db = temp / "memory" / "memory.db"
        report["memorySnapshot"] = sqlite_snapshot(settings.memory_db, snapshot_db)
        source_vector = settings.memory_db.with_name(f"{settings.memory_db.stem}-vectors-512.db")
        if source_vector.is_file():
            report["vectorSnapshot"] = sqlite_snapshot(
                source_vector, snapshot_db.with_name("memory-vectors-512.db")
            )
        dense_dir = (
            settings.memory_model_dir or settings.memory_db.parent / "models" / "bge-small-zh-v1.5"
        )
        report["denseAssetsPresent"] = (dense_dir / "onnx" / "model_quantized.onnx").is_file()
        sessions = JsonlSessionStore(settings.sessions_dir)
        search = MemorySearchService(
            [SessionHistorySource(sessions)],
            source_order_provider=SessionStoreSourceOrderProvider(sessions),
        )
        memory = build_memory(
            snapshot_db, ForbiddenExtractor(), search_service=search, dense_model_dir=dense_dir
        )
        budget = WireBudget(evidence)
        runtime = resolve_bridge_runtime_config(credential_source_for_mode(config_mode_from_env()))
        ledger = NotificationStore(temp / "notifications.db")

        def model_factory(config: ModelRuntimeConfig) -> LanguageModel:
            assert budget.current is not None
            budget.current.update(model=config.model_id, preset=config.reasoning_preset)
            model = runtime.llm_client_factory(config)
            spec = model.spec
            budget.evidence.secret = spec.api_key
            base = spec.api_base or (
                spec.responses.api_base if spec.responses is not None else None
            )
            if base is None:
                raise SmokeBoundaryExceeded("model endpoint has no explicit catalog base URL")
            url = urlsplit(base)
            path = url.path.rstrip("/") + (
                "/responses" if spec.responses is not None else "/chat/completions"
            )
            budget.endpoint = (url.hostname or "", path)
            return ObservedModel(model, budget.current, budget)

        generator = NotificationGenerator(
            reader=reader,
            factory=model_factory,
            persona=persona,
            memory=cast(Memory, ReadOnlyMemory(memory, budget)),
        )
        async_send = httpx.AsyncHTTPTransport.handle_async_request
        sync_send = httpx.HTTPTransport.handle_request

        async def guarded_async(
            transport: httpx.AsyncHTTPTransport, request: httpx.Request
        ) -> httpx.Response:
            budget.observe(request)
            return await async_send(transport, request)

        def guarded_sync(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
            budget.observe(request)
            return sync_send(transport, request)

        try:
            memory.warmup()
            with (
                patch.object(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async),
                patch.object(httpx.HTTPTransport, "handle_request", guarded_sync),
            ):
                for case_index, fact in enumerate(facts(), start=1):
                    record: dict[str, Any] = {
                        "caseIndex": case_index,
                        "kind": fact.kind,
                        "logicalCalls": 0,
                        "wireRequests": 0,
                        "personaRevision": effective.revision,
                    }
                    report["cases"].append(record)
                    budget.current = record
                    item = ledger.accept(fact)
                    ledger.claim_next()
                    started = time.monotonic()
                    try:
                        text = generator.generate(
                            fact, stopping=threading.Event(), still_valid=lambda: True
                        )
                        record.update(text=text, source="generated", generationCompleted=True)
                    except (Exception, SmokeBoundaryExceeded) as error:
                        record.update(
                            text=fallback_text(fact),
                            source="fallback",
                            generationCompleted=False,
                            errorType=type(error).__name__,
                        )
                    record["elapsedMs"] = round((time.monotonic() - started) * 1000, 2)
                    ledger.ready(item.notification_id, record["text"], source=record["source"])
                    report["physicalRequests"] = budget.total
                    output.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            report["allGenerationsCompleted"] = all(
                case["generationCompleted"] for case in report["cases"]
            )
            report["semanticReview"] = "required; completion does not assert factual correctness"
        finally:
            memory.close()
    report["finishedAt"] = datetime.now(UTC).isoformat()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New absolute report path outside Git; private input artifacts are saved beside it",
    )
    args = parser.parse_args()
    if not args.allow_llm and not args.prepare_only:
        parser.error("requires --prepare-only or separately authorized --allow-llm (three calls)")
    try:
        args.output = private_output(args.output)
    except ValueError as error:
        parser.error(str(error))
    if args.prepare_only:
        print(json.dumps(run(args.output, allow_llm=False), ensure_ascii=False, indent=2))
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write('{"status":"reserved-before-calls"}')
    result = run(args.output, allow_llm=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("allGenerationsCompleted") else 1


if __name__ == "__main__":
    raise SystemExit(main())

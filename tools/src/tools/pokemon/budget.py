"""Persistent model-call budget shared by gameplay and context compaction."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from agent.runtime.cancellation import Cancellation, CancelReason, RunCancelled

from llm_providers import LanguageModel, LLMClient, ModelRequest, ModelResponse

from .runtime.protocol import atomic_json


class PlayStopped(RunCancelled):
    def __init__(self, reason: str) -> None:
        super().__init__(Cancellation(CancelReason.EXPLICIT_REQUEST, datetime.now(UTC)))
        self.stop_reason = reason

    def __str__(self) -> str:
        return self.stop_reason


class PlayBudget:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "play.json"
        self.lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.path.read_text(encoding="utf-8")))

    def check(self) -> None:
        data = self.read()
        if (self.root / "paused").exists() or (self.root / "finish.json").exists():
            raise PlayStopped("play_paused")
        if data["calls_reserved"] >= data["max_calls"] or time.time() >= data["deadline"]:
            raise PlayStopped("play_budget_exhausted")

    def reserve(self, request: ModelRequest) -> ModelRequest:
        with self.lock:
            self.check()
            data = self.read()
            data["calls_reserved"] += 1
            atomic_json(self.path, data)  # reserve before any provider request, including crashes
            remaining = max(0.0, data["deadline"] - time.time())
            deadline = time.monotonic() + remaining
            if request.control.deadline_monotonic is not None:
                deadline = min(deadline, request.control.deadline_monotonic)
            return replace(
                request,
                control=replace(
                    request.control,
                    allow_retries=False,
                    retry_limit=0,
                    allow_output_retries=False,
                    deadline_monotonic=deadline,
                ),
            )

    def record(self, elapsed: float, usage: Any) -> None:
        with self.lock:
            data = self.read()
            data["model_seconds"] = data.get("model_seconds", 0.0) + elapsed
            data.setdefault("usage", []).append(asdict(usage) if usage is not None else None)
            atomic_json(self.path, data)


class BudgetModel(LLMClient):
    def __init__(self, model: LanguageModel, budget: PlayBudget) -> None:
        if not isinstance(model, LLMClient):
            raise TypeError("BudgetModel requires a bound AF LLMClient")
        super().__init__(model.spec)
        self.model, self.budget = model, budget

    def __getattr__(self, name: str) -> Any:
        return getattr(self.model, name)

    @contextmanager
    def bind_execution(self) -> Iterator[BudgetModel]:
        # The runner has already bound the underlying connection for this play episode.
        # Preserve rejection state as well as the shared budget across AF turn snapshots.
        yield self

    def generate(self, request: ModelRequest) -> ModelResponse:
        request = self.budget.reserve(request)
        start = time.monotonic()
        result = None
        try:
            result = self.model.generate(request)
            return result
        finally:
            self.budget.record(time.monotonic() - start, result.usage if result else None)

    def stream(
        self,
        request: ModelRequest | list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[Any]:
        if not isinstance(request, ModelRequest):
            request = ModelRequest(messages=request, tools=tools, settings=overrides)
        elif tools is not None or overrides:
            raise ValueError("Do not combine ModelRequest with legacy stream arguments")
        request = self.budget.reserve(request)
        start, usage = time.monotonic(), None
        try:
            for event in self.model.stream(request):
                if getattr(event, "usage", None) is not None:
                    usage = getattr(event, "usage", None)
                yield event
        finally:
            self.budget.record(time.monotonic() - start, usage)

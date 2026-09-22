"""Content-free logical-call, HTTP-send and retry-plan accounting."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from functools import wraps
from hashlib import sha256
from typing import Any
from uuid import uuid4

from .errors import LLMError, LLMNetworkError, LLMTransientProviderError
from .stream_events import LLMTurnDone

logger = logging.getLogger(__name__)


@dataclass
class CallObservation:
    operation: str
    session_ref: str | None = None
    run_ref: str | None = None
    route_ref: str | None = None
    call_id: str = field(default_factory=lambda: uuid4().hex)
    http_attempts: int = 0
    retry_plans: int = 0
    retry_stop: str | None = None
    timeout_policy: dict[str, Any] | None = None
    business_deadline: bool = False
    business_remaining_seconds: float | None = None

    def plan_retry(self, error: LLMError, delay: float) -> None:
        self.retry_plans += 1
        logger.info(
            "model_retry_plan %s",
            json.dumps(
                {
                    "call_id": self.call_id,
                    "retry_plan": self.retry_plans,
                    "delay_seconds": delay,
                    "error": asdict(error.error_evidence) if error.error_evidence else None,
                },
                ensure_ascii=True,
            ),
        )

    def sent(self) -> None:
        self.http_attempts += 1
        logger.info("model_http_attempt call_id=%s attempt=%d", self.call_id, self.http_attempts)

    def finish(self, error: BaseException | None) -> None:
        reason = "completed"
        evidence = error.error_evidence if isinstance(error, LLMError) else None
        if error is not None:
            name = type(error).__name__.lower()
            if "deadline" in name or type(error) is TimeoutError:
                reason = "deadline"
            elif "cancel" in name or isinstance(error, GeneratorExit):
                reason = "cancelled"
            elif isinstance(error, (LLMNetworkError, LLMTransientProviderError)):
                reason = self.retry_stop or "retry_stopped"
            else:
                reason = "not_retryable"
        logger.info(
            "model_call_observation %s",
            json.dumps(
                {
                    **asdict(self),
                    "schema_revision": 1,
                    "stop_reason": reason,
                    "error": asdict(evidence) if evidence else None,
                },
                ensure_ascii=True,
            ),
        )


current_call: ContextVar[CallObservation | None] = ContextVar("model_call", default=None)


def _new(args: tuple[Any, ...], kwargs: dict[str, Any]) -> CallObservation:
    request = args[1] if len(args) > 1 else kwargs.get("request")
    trace = getattr(getattr(request, "control", None), "trace_context", None)
    operation = getattr(trace, "operation", "unspecified")
    allowed = {
        "conversation",
        "context_compaction",
        "memory_extraction",
        "memory_reconciliation",
        "notification",
        "unspecified",
    }

    def digest(value: str | None) -> str | None:
        return sha256(value.encode()).hexdigest()[:24] if value else None

    spec = getattr(args[0], "spec", None) if args else None
    route = "\x1f".join(str(getattr(spec, key, "")) for key in ("provider_id", "api_base", "model"))
    return CallObservation(
        operation if operation in allowed else "other",
        session_ref=digest(getattr(trace, "session_id", None)),
        run_ref=digest(getattr(trace, "run_id", None)),
        route_ref=digest(route),
    )


def observed_generate[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> T:
        observation = _new(args, kwargs)
        token = current_call.set(observation)
        error: BaseException | None = None
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:
            error = exc
            raise
        finally:
            current_call.reset(token)
            observation.finish(error)

    return run


def observed_stream[**P, T](fn: Callable[P, Iterator[T]]) -> Callable[P, Iterator[T]]:
    @wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> Iterator[T]:
        observation = _new(args, kwargs)
        # stream() may resolve local policy before returning its lazy iterator.
        token = current_call.set(observation)
        try:
            upstream = fn(*args, **kwargs)
        except BaseException as exc:
            observation.finish(exc)
            raise
        finally:
            current_call.reset(token)

        def iterate() -> Iterator[T]:
            error: BaseException | None = None
            completed = False
            try:
                while True:
                    token = current_call.set(observation)
                    try:
                        item = next(upstream)
                    except StopIteration:
                        return
                    finally:
                        current_call.reset(token)
                    if isinstance(item, LLMTurnDone):
                        completed = True
                    yield item
            except BaseException as exc:
                if not (completed and isinstance(exc, GeneratorExit)):
                    error = exc
                raise
            finally:
                close = getattr(upstream, "close", None)
                if callable(close):
                    close()
                observation.finish(error)

        return iterate()

    return run

"""Global approval routing, exact-action review caching and denial circuit breaker."""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .reviewer import ReviewAssessment


def action_payload(request: Any) -> dict[str, Any]:
    value = request.to_dict()
    return {
        key: value[key] for key in ("kind", "reason", "details", "items", "actions") if key in value
    }


def action_fingerprint(action: dict[str, Any]) -> str:
    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: normalize(item)
                for key, item in value.items()
                if key not in {"reason", "item_id", "action_id"}
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    return hashlib.sha256(
        json.dumps(normalize(action), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class AutomaticApprovals:
    """Called only at an existing capability boundary; never creates reusable grants."""

    def __init__(
        self,
        *,
        policy: Callable[[], tuple[str, int]],
        review: Callable[[Any, dict[str, Any], Callable[[], None], float], ReviewAssessment],
        stop_run: Callable[[str, str], None] = lambda _session, _run: None,
    ) -> None:
        self.policy = policy
        self.review = review
        self.stop_run = stop_run
        self._lock = threading.Lock()
        self._history: dict[tuple[str, str], deque[bool]] = {}
        self._denials: dict[tuple[str, str, str], ReviewAssessment] = {}
        self._closed: set[tuple[str, str]] = set()
        self._receipts: dict[str, Any] = {}
        self._reauthorizations: dict[tuple[str, str], dict[str, Any]] = {}
        self._used_retries: set[str] = set()

    def mode(self) -> tuple[str, int]:
        try:
            mode, version = self.policy()
            return (mode if mode in {"full_access", "auto_review"} else "auto_review", version)
        except Exception:
            return "auto_review", 0

    def reauthorize(
        self, request_id: str, session_id: str, *, expected_version: int
    ) -> dict[str, Any]:
        """Host/UI only: grant one new review of this exact rejected action, never execute it."""
        with self._lock:
            request = self._receipts.get(request_id)
            if (
                request is None
                or request.session_id != session_id
                or request.version != expected_version
                or request.status not in {"denied", "failed", "timed_out"}
                or request_id in self._used_retries
            ):
                raise ValueError("review request is stale")
            action = action_payload(request)
            fingerprint = action_fingerprint(action)
            self._reauthorizations[(session_id, fingerprint)] = {
                "action": action,
                "previous_review": request.review_assessment,
                "authorization": "The user explicitly authorizes one retry of this exact action after seeing the prior review. Reassess; this is not an unconditional grant.",
                "expires_at": time.monotonic() + 600,
            }
            self._used_retries.add(request_id)
            return {"action": action, "fingerprint": fingerprint}

    def decide(
        self,
        request: Any,
        *,
        timeout: float,
        publish: Callable[[], None],
        policy_current: Callable[[], bool],
        manual_fallback: Callable[[str, ReviewAssessment | None], bool] | None = None,
    ) -> Any:
        def finish(state: str, assessment: ReviewAssessment | None) -> Any:
            if request._event.is_set() or not policy_current():
                state = "aborted"
            if (
                state in {"failed", "timed_out", "denied"}
                and (assessment is None or assessment.risk_level != "critical")
                and manual_fallback is not None
                and manual_fallback(state, assessment)
            ):
                return request._decision
            return self._finish(request, state, assessment, publish)

        mode = request.approval_mode
        request.decision_source = "auto_review" if mode == "auto_review" else "full_access"
        action = action_payload(request)
        fingerprint = action_fingerprint(action)
        request.review_fingerprint = fingerprint
        key = (request.session_id, request.run_id)
        context = request.review_context
        user_evidence = [
            item
            for item in getattr(context, "transcript", [])
            if item.get("event_type") == "user_message" and not item.get("source")
        ]
        evidence_key = hashlib.sha256(
            json.dumps(user_evidence, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        denial_key = (request.session_id, evidence_key, fingerprint)
        request.review_cache_key = denial_key
        if request.risk_assessment is not None and request.risk_assessment.catastrophic:
            request.decision_source = "catastrophic_policy"
            return self._finish(request, "denied", None, publish)
        if mode == "full_access":
            # A settings change during preparation must not retain a broad grant.
            if not policy_current():
                return self._finish(request, "aborted", None, publish)
            return self._finish(request, "approved", None, publish)
        with self._lock:
            closed = key in self._closed
            cached = self._denials.get(denial_key)
            extra = self._reauthorizations.pop((request.session_id, fingerprint), None)
        if closed:
            return self._finish(request, "denied", None, publish, remember=False)
        if extra is not None and extra.pop("expires_at") > time.monotonic():
            action["host_user_reauthorization"] = extra
        elif cached is not None:
            return finish("denied", cached)
        request.status = "reviewing"
        request.version += 1
        publish()
        deadline = time.monotonic() + min(timeout, 60.0)
        result: queue.Queue[ReviewAssessment | Exception] = queue.Queue(maxsize=1)
        stopped = threading.Event()

        def check() -> None:
            if stopped.is_set() or request._event.is_set():
                raise InterruptedError("review aborted")
            if time.monotonic() >= deadline:
                raise TimeoutError("review timed out")

        def work() -> None:
            try:
                result.put(self.review(request, action, check, deadline))
            except Exception as error:
                result.put(error)

        threading.Thread(target=work, name="approval-review", daemon=True).start()
        while True:
            try:
                check()
                assessment = result.get(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            except (InterruptedError, TimeoutError) as error:
                stopped.set()
                return finish(
                    "aborted" if isinstance(error, InterruptedError) else "timed_out",
                    None,
                )
            stopped.set()
            if isinstance(assessment, Exception):
                state = "timed_out" if isinstance(assessment, TimeoutError) else "failed"
                return finish(state, None)
            if not policy_current() or request._event.is_set():
                return self._finish(request, "aborted", None, publish)
            state = "approved" if assessment.outcome == "allow" else "denied"
            return finish(state, assessment)

    def _finish(
        self,
        request: Any,
        state: str,
        assessment: ReviewAssessment | None,
        publish: Callable[[], None],
        *,
        remember: bool = True,
    ) -> Any:
        request.status = state
        request.review_assessment = assessment.to_dict() if assessment else None
        request._decision = (
            request.fully_approved_decision()
            if state == "approved"
            else request.cancelled_decision()
        )
        request.version += 1
        request._event.set()
        stop = False
        if remember:
            with self._lock:
                # Review context contains a live model binding and transient evidence;
                # receipts retain the action/assessment only, not that capability.
                request.review_context = None
                self._receipts[request.request_id] = request
                # Only keep recent receipts for user retry; durable audit is the existing event stream.
                if len(self._receipts) > 500:
                    removed = next(iter(self._receipts))
                    self._receipts.pop(removed)
                    self._used_retries.discard(removed)
                if request.decision_source == "auto_review":
                    key = (request.session_id, request.run_id)
                    history = self._history.setdefault(key, deque(maxlen=50))
                    history.append(state == "denied")
                    if state == "denied" and assessment is not None:
                        self._denials[request.review_cache_key] = assessment
                        if len(self._denials) > 500:
                            self._denials.pop(next(iter(self._denials)))
                    stop = sum(history) >= 10 or (len(history) >= 3 and all(list(history)[-3:]))
                    if stop:
                        self._closed.add(key)
        publish()
        if stop:
            self.stop_run(request.session_id, request.run_id)
        return request._decision

    def finish_run(self, session_id: str, run_id: str) -> None:
        with self._lock:
            key = (session_id, run_id)
            self._history.pop(key, None)
            self._closed.discard(key)

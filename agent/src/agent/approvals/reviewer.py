"""Independent, bounded security review. No persona, memory or execution tools."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

from llm_providers import LanguageModel, ModelCallControl, ModelCallTraceContext, ModelRequest

ReviewRisk = Literal["low", "medium", "high", "critical"]
Authorization = Literal["unknown", "low", "medium", "high"]

REVIEW_CONTEXT_ARG = "__agent_friend_review_context"


def review_failure_message(status: str) -> str:
    reason = {
        "failed": "自动审批过程出错，未能取得审批结论。",
        "timed_out": "自动审批超时，未能取得审批结论。",
        "aborted": "自动审批已中止。",
        "denied": "本次操作被自动审批拒绝。",
    }.get(status, "自动审批未完成。")
    return (
        reason + "未授予权限，操作未执行。不要原样重试或换工具绕过；"
        "普通会话可由用户对这项操作发起重新审批，自主活动可选择安全替代或结束。"
    )


@dataclass(frozen=True)
class ReviewContext:
    model: LanguageModel
    transcript: list[dict[str, Any]]


def visible_review_transcript(events: list[Any]) -> list[dict[str, Any]]:
    """Keep provenance, omit hidden reasoning, bound public evidence without inventing consent."""
    output: list[dict[str, Any]] = []
    used = 0
    for event in reversed(events):
        if event.type not in {
            "user_message",
            "assistant_message",
            "tool_call_request",
            "tool_call_result",
            "system_trigger",
        }:
            continue
        payload = event.payload
        item = {
            "event_type": event.type,
            "event_id": event.uuid,
            "source": event.meta.get("message_source") or payload.get("request_source"),
            "content": {
                key: payload[key]
                for key in (
                    "content",
                    "text",
                    "tool_name",
                    "name",
                    "arguments",
                    "result",
                    "source_kind",
                    "prompt",
                    "reason",
                )
                if key in payload
            },
        }
        serialized = json.dumps(item, ensure_ascii=False)
        if len(serialized) > 6000:
            item["content"] = {"excerpt": serialized[:6000], "omitted": "<guardian_truncated />"}
        used += min(6000, len(serialized))
        if used > 30000:
            output.append({"omitted": "<guardian_truncated prior_history />"})
            break
        output.append(item)
    return list(reversed(output))


def review_pending_action(
    request: Any, action: dict[str, Any], check: Callable[[], None], deadline: float
) -> ReviewAssessment:
    context = request.review_context
    if not isinstance(context, ReviewContext):
        raise ValueError("trusted review context unavailable")
    paths: list[Path] = []
    for item in action.get("items", []):
        if isinstance(item.get("path"), str):
            paths.append(Path(item["path"]))
    for item in action.get("actions", []):
        if isinstance(item.get("target"), str):
            paths.append(Path(item["target"]))
    details = action.get("details", {})
    if isinstance(details.get("workdir"), str):
        paths.append(Path(details["workdir"]))
    return SecurityReviewer(context.model).review(
        action,
        context.transcript,
        inspect=lambda path, offset: inspect_action_path(path, offset, allowed_paths=tuple(paths)),
        cancel_check=check,
        deadline=deadline,
        session_id=request.session_id,
        run_id=request.run_id,
    )


@dataclass(frozen=True)
class ReviewAssessment:
    outcome: Literal["allow", "deny"]
    risk_level: ReviewRisk
    user_authorization: Authorization
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_assessment(raw: str) -> ReviewAssessment:
    """Codex's outcome-only default plus bounded recovery of a wrapped JSON object."""
    value = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    if not isinstance(value, dict) or set(value) - {
        "outcome",
        "risk_level",
        "user_authorization",
        "rationale",
    }:
        raise ValueError("invalid review fields")
    outcome = value.get("outcome")
    if outcome not in {"allow", "deny"}:
        raise ValueError("review requires allow/deny outcome")
    risk = value.get("risk_level", "low" if outcome == "allow" else "high")
    authorization = value.get("user_authorization", "unknown")
    rationale = value.get(
        "rationale", "Allowed low-risk action." if outcome == "allow" else "Action denied."
    )
    if (
        risk not in {"low", "medium", "high", "critical"}
        or authorization not in {"unknown", "low", "medium", "high"}
        or not isinstance(rationale, str)
        or not rationale.strip()
    ):
        raise ValueError("invalid review assessment")
    # A malformed model conclusion must never override the policy's hard thresholds.
    if risk == "critical" or (risk == "high" and authorization not in {"medium", "high"}):
        outcome = "deny"
    return ReviewAssessment(
        cast(Literal["allow", "deny"], outcome), risk, authorization, rationale[:2000]
    )


class SecurityReviewer:
    def __init__(
        self,
        model: LanguageModel,
        *,
        before_call: Callable[[], None] = lambda: None,
        record_usage: Callable[[object], None] = lambda _usage: None,
    ) -> None:
        self.model = model
        self.before_call = before_call
        self.record_usage = record_usage

    def review(
        self,
        action: dict[str, Any],
        transcript: list[dict[str, Any]],
        *,
        inspect: Callable[[str, int], dict[str, Any]],
        cancel_check: Callable[[], None],
        deadline: float,
        session_id: str,
        run_id: str,
    ) -> ReviewAssessment:
        resources = Path(__file__).parent / "resources"
        prompt = (
            (resources / "reviewer.md")
            .read_text(encoding="utf-8")
            .replace(
                "{{ tenant_policy_config }}", (resources / "policy.md").read_text(encoding="utf-8")
            )
            .replace("{{ extra_policy }}", "")
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {"action": action, "transcript": transcript}, ensure_ascii=False
                ),
            },
        ]
        for index in range(3):
            cancel_check()
            if time.monotonic() >= deadline:
                raise TimeoutError("review deadline exceeded")
            self.before_call()
            response = self.model.generate(
                ModelRequest(
                    messages=messages,
                    control=ModelCallControl(
                        cancel_check=cancel_check,
                        deadline_monotonic=deadline,
                        allow_retries=True,
                        retry_limit=1,
                        trace_context=ModelCallTraceContext(
                            operation="approval_review",
                            call_kind="security_review",
                            session_id=session_id,
                            run_id=run_id,
                            call_index=index,
                        ),
                    ),
                )
            )
            self.record_usage(response.usage)
            cancel_check()
            if time.monotonic() >= deadline:
                raise TimeoutError("review deadline exceeded")
            try:
                value = json.loads(response.text)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict) and set(value) == {"inspect"}:
                request = value["inspect"]
                if index == 2 or not isinstance(request, dict) or set(request) - {"path", "offset"}:
                    raise ValueError("inspection budget or schema exceeded")
                path, offset = request.get("path"), request.get("offset", 0)
                if not isinstance(path, str) or type(offset) is not int or offset < 0:
                    raise ValueError("invalid inspection request")
                result = inspect(path, offset)
                messages.extend(
                    [
                        {"role": "assistant", "content": response.text},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"host_readonly_inspection": result}, ensure_ascii=False
                            ),
                        },
                    ]
                )
                continue
            return parse_assessment(response.text)
        raise ValueError("review did not finish")


def inspect_action_path(
    path: str, offset: int, *, allowed_paths: tuple[Path, ...]
) -> dict[str, Any]:
    """No shell/network; exact action paths and descendants of its explicit workdir only."""
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        return {"error": "inspection_path_not_allowed"}
    resolved = candidate.resolve()
    if not any(
        resolved == root.resolve() or (root.is_dir() and resolved.is_relative_to(root.resolve()))
        for root in allowed_paths
    ):
        return {"error": "inspection_path_not_allowed"}
    if any(
        part.lower()
        in {".ssh", ".aws", ".azure", ".gnupg", "credentials", "credentials.json", ".env"}
        for part in resolved.parts
    ):
        return {"error": "credential_contents_unavailable"}
    try:
        if not resolved.exists():
            return {"path": path, "exists": False}
        if resolved.is_dir():
            entries = []
            for child in resolved.iterdir():
                entries.append(
                    {"name": child.name, "directory": child.is_dir(), "symlink": child.is_symlink()}
                )
                if len(entries) == 201:
                    break
            return {"path": path, "entries": entries[:200], "truncated": len(entries) > 200}
        if not resolved.is_file() or offset > 1024 * 1024:
            return {"error": "inspection_file_unavailable"}
        with resolved.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(8193)
        return {
            "path": path,
            "offset": offset,
            "content": data[:8192].decode("utf-8", errors="replace"),
            "truncated": len(data) > 8192,
            "next_offset": offset + min(len(data), 8192),
        }
    except OSError:
        return {"error": "inspection_unavailable"}

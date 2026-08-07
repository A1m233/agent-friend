"""agent 行为契约评测的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FixtureAttachment:
    """Project-owned local asset attached to an eval message."""

    path: Path
    display_name: str


@dataclass(frozen=True)
class FixtureMessage:
    """fixture session 中一条历史消息。"""

    role: str
    text: str
    attachments: list[FixtureAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class FixtureToolCallRequest:
    """A historical tool request already completed before the evaluated turn."""

    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FixtureToolCallResult:
    """The historical result paired with :class:`FixtureToolCallRequest`."""

    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False


FixtureCurrentEvent = FixtureMessage | FixtureToolCallRequest | FixtureToolCallResult


@dataclass(frozen=True)
class FixtureSession:
    """一条伪过去对话，跑 case 前注入到 SessionStore。"""

    days_ago: int
    messages: list[FixtureMessage]
    title: str | None = None


@dataclass(frozen=True)
class FixtureSkill:
    """A minimal case-local Skill package discovered by the production loader."""

    name: str
    description: str
    instructions: str
    allow_implicit_invocation: bool = True


@dataclass(frozen=True)
class FakeToolResponse:
    """One deterministic fixture response selected by a subset of call arguments."""

    match_args: dict[str, Any]
    result_text: str
    is_error: bool = False
    source_urls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FakeTool:
    """case 专用的固定结果工具，不访问真实外部服务。"""

    name: str
    result_text: str = ""
    is_error: bool = False
    source_urls: list[str] = field(default_factory=list)
    responses: list[FakeToolResponse] = field(default_factory=list)


@dataclass(frozen=True)
class RequiredSuccessfulToolCall:
    """A successful tool call matching public args and optional result evidence."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    result_contains: str | None = None


@dataclass(frozen=True)
class CaseJudge:
    """case 级 deterministic 判分规则。"""

    must_call: str | None = None
    must_not_call_any: list[str] = field(default_factory=list)
    must_not_succeed_any: list[str] = field(default_factory=list)
    require_non_empty_response: bool = False
    forbid_question: bool = False
    forbid_question_with_scheduled_action: bool = False
    must_mention_any: list[str] = field(default_factory=list)
    must_mention_all: list[str] = field(default_factory=list)
    must_not_mention_any: list[str] = field(default_factory=list)
    forbid_memory_mechanism_terms: bool = False
    forbid_unsupported_lookup_claim: bool = False
    forbid_memory_mismatch_story: bool = False
    forbid_unsupported_source_attribution: bool = False
    required_tool_sequence: list[str] = field(default_factory=list)
    required_successful_tool_calls: list[RequiredSuccessfulToolCall] = field(default_factory=list)
    require_progress_continuity: bool = False
    forbid_tool_mechanism_terms: bool = False
    forbid_unsupported_collection_characterization: bool = False
    forbid_play_count_scope_inflation: bool = False
    forbid_unprompted_user_intent_projection: bool = False
    forbid_unprompted_self_evaluation: bool = False
    forbid_unsupported_model_motive_story: bool = False
    forbid_unsupported_future_action_commitment: bool = False
    forbid_unsupported_skill_activation_claim: bool = False


@dataclass(frozen=True)
class Case:
    """单条 query fixture。"""

    id: str
    query: str
    facet: str
    source: str
    runtime_now: datetime | None = None
    surface: str | None = None
    forbidden_terms: list[str] = field(default_factory=list)
    fixture_session: FixtureSession | None = None
    fixture_skills: list[FixtureSkill] = field(default_factory=list)
    current_session_messages: list[FixtureMessage] = field(default_factory=list)
    current_session_events: list[FixtureCurrentEvent] = field(default_factory=list)
    query_attachments: list[FixtureAttachment] = field(default_factory=list)
    fake_tools: list[FakeTool] = field(default_factory=list)
    judge: CaseJudge | None = None


@dataclass(frozen=True)
class Contract:
    """一组共享行为契约的 case。"""

    id: str
    name: str
    expected_behavior: str
    expected_tool: str | None
    required_in_response: str | None
    cases: list[Case]


@dataclass(frozen=True)
class ToolCallTrace:
    """单次 tool call 的完整快照。"""

    name: str
    args: dict[str, Any]
    result_text: str
    is_error: bool
    duration_seconds: float | None = None


@dataclass(frozen=True)
class Trace:
    """跑完一个 case 后收集到的 agent 行为。"""

    case_id: str
    tool_calls: list[ToolCallTrace]
    final_text: str
    stop_reason: str
    assistant_segments: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class Verdict:
    """case 级判分结果。"""

    case_id: str
    contract_id: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class CaseSetMeta:
    """case 集元信息，写入 report / baseline 便于追溯。"""

    digest: str
    total_contracts: int
    total_cases: int
    contract_counts: dict[str, int]

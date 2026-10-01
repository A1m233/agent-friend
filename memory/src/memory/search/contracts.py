"""统一记忆搜索的公共契约。

``memory`` 只定义查询、候选与数据源协议，不认识 ``agent.sessions``。原始会话
由 agent 侧适配为 :class:`EvidenceCandidate`，从而让自动召回、显式回忆与
inspector 共享同一套查询语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

SearchMode = Literal["automatic", "explicit", "probe"]
RetrievalGoal = Literal["search", "summary"]
SearchIntent = Literal[
    "general",
    "identity",
    "history",
    "temporal",
    "current_state",
    "historical_state",
    "last_interaction",
]
EvidenceKind = Literal["core", "semantic", "episodic", "utterance"]
EvidenceSpeaker = Literal["user", "agent"]
SaidBy = Literal["user", "agent"]
RecallScopeStatus = Literal["eligible", "suppressed"]
RecallScopeOrderStatus = Literal[
    "not_applied",
    "not_required",
    "ready",
    "unavailable",
    "cutoff_unresolved",
]


@dataclass(frozen=True)
class CurrentSessionRecallScope:
    """Automatic recall range for one active conversation session.

    Object presence enables the scope.  A ``None`` boundary deliberately means
    that no ordinary evidence from the current session is eligible yet.
    """

    current_session_id: str
    recall_through_event_uuid: str | None

    def __post_init__(self) -> None:
        if not self.current_session_id.strip():
            raise ValueError("current_session_id 不能为空")
        if (
            self.recall_through_event_uuid is not None
            and not self.recall_through_event_uuid.strip()
        ):
            raise ValueError("recall_through_event_uuid 不能为空字符串")


class SessionSourceOrderProvider(Protocol):
    """Project an active conversation lineage without exposing session types."""

    def active_conversation_event_uuids(
        self,
        session_id: str,
    ) -> tuple[str, ...] | None: ...


@dataclass(frozen=True)
class TimeRange:
    """半开时间区间 ``[since, until)``。"""

    since: datetime | None = None
    until: datetime | None = None

    def __post_init__(self) -> None:
        if self.since is not None and self.since.tzinfo is None:
            raise ValueError("since 必须是 timezone-aware datetime")
        if self.until is not None and self.until.tzinfo is None:
            raise ValueError("until 必须是 timezone-aware datetime")
        if self.since is not None and self.until is not None and self.since >= self.until:
            raise ValueError("since 必须早于 until")


@dataclass(frozen=True)
class SearchRequest:
    """调用方提供的原始搜索条件；intent/token/time 推断由 analyzer 负责。"""

    text: str
    owner_user_id: str
    persona_id: str
    mode: SearchMode
    session_id: str | None = None
    top_k: int | None = None
    said_by: SaidBy | None = None
    explicit_time_range: TimeRange | None = None
    source_scope: frozenset[str] | None = None
    current_turn_start_index: int | None = None
    target_session_ids: tuple[str, ...] = ()
    retrieval_goal: RetrievalGoal = "search"
    automatic_recall_scope: CurrentSessionRecallScope | None = None
    current_turn_start_event_uuid: str | None = None

    def __post_init__(self) -> None:
        if self.top_k is not None and self.top_k <= 0:
            raise ValueError("top_k 必须大于 0")
        if self.current_turn_start_index is not None and self.current_turn_start_index < 0:
            raise ValueError("current_turn_start_index 不能小于 0")
        if self.automatic_recall_scope is not None:
            if self.mode != "automatic":
                raise ValueError("automatic_recall_scope 只允许用于 automatic mode")
            if (
                self.session_id is not None
                and self.session_id != self.automatic_recall_scope.current_session_id
            ):
                raise ValueError("session_id 与 automatic_recall_scope 不一致")


@dataclass(frozen=True)
class AnalyzedQuery:
    """一次确定性 query 分析的结果。"""

    raw_text: str
    retrieval_text: str
    intent: SearchIntent
    time_range: TimeRange | None
    identity_requested: bool
    include_historical: bool
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceExcerpt:
    """候选附带的只读原话投影，供显式工具自然渲染上下文。"""

    speaker: EvidenceSpeaker
    text: str
    occurred_at: datetime


@dataclass(frozen=True)
class EvidenceCandidate:
    """所有数据源统一返回的只读证据候选。"""

    evidence_id: str
    source_name: str
    kind: EvidenceKind
    text: str
    owner_user_id: str
    persona_id: str | None
    occurred_at: datetime | None
    valid_from: datetime | None
    valid_until: datetime | None
    importance: float
    provenance: tuple[str, ...]
    keyword_rank: int | None = None
    dense_rank: int | None = None
    fused_score: float | None = None
    excerpts: tuple[EvidenceExcerpt, ...] = ()
    session_id: str | None = None


class EvidenceSource(Protocol):
    """统一搜索编排层可注册的数据源。"""

    name: str

    def search(
        self,
        query: AnalyzedQuery,
        *,
        request: SearchRequest,
        limit: int,
    ) -> list[EvidenceCandidate]: ...


@dataclass(frozen=True)
class SourceFailure:
    """单个 source 的降级记录；message 只用于内部日志/测试，不直接展示用户。"""

    source_name: str
    message: str


@dataclass(frozen=True)
class SourceRun:
    """一次 source 调度摘要，供 trace / inspector 解释回退策略。"""

    source_name: str
    candidate_count: int
    limit: int
    status: Literal["ok", "failed", "skipped"] = "ok"
    truncated: bool = False
    raw_candidate_count: int | None = None
    suppressed_count: int = 0
    scope_scan_attempts: int = 1
    scope_scan_truncated: bool = False


@dataclass(frozen=True)
class RecallScopeDecision:
    """Automatic scope decision, independent from final fusion/quota."""

    evidence_id: str
    status: RecallScopeStatus
    reason: str
    current_session_source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateDecision:
    """融合阶段对候选做出的最终决策。"""

    evidence_id: str
    decision: Literal["injected", "duplicate", "quota"]
    reason: str


@dataclass(frozen=True)
class SearchResult:
    """统一搜索结果与可解释融合摘要。"""

    request: SearchRequest
    query: AnalyzedQuery
    evidence: tuple[EvidenceCandidate, ...]
    failures: tuple[SourceFailure, ...] = field(default_factory=tuple)
    source_runs: tuple[SourceRun, ...] = field(default_factory=tuple)
    decisions: tuple[CandidateDecision, ...] = field(default_factory=tuple)
    candidates: tuple[EvidenceCandidate, ...] = field(default_factory=tuple)
    scope_decisions: tuple[RecallScopeDecision, ...] = field(default_factory=tuple)
    scope_order_status: RecallScopeOrderStatus = "not_applied"
    truncated: bool = False

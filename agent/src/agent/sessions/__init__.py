"""``agent.sessions`` —— 会话管理子包。

引擎层一等公民"会话"的完整实现：事件 schema / Session 聚合根 / 持久化协议 +
JSONL 实现 / 业务编排管理器 / 异常体系。

详见 docs/requirements/002-engine-session-management/。
"""

from .cache_epoch import (
    CacheEnvelopeState,
    CacheEpochUpdate,
    PreparedCacheEpoch,
    SessionCacheEpoch,
)
from .catalog import (
    SessionCatalog,
    SessionDescriptor,
    SessionResolution,
    normalize_session_title,
)
from .changes import (
    SessionChange,
    SessionChangeBroker,
    SessionMutationContext,
    SessionRevisionStore,
    current_session_mutation_context,
    session_mutation_context,
)
from .errors import (
    SessionCorruptError,
    SessionError,
    SessionNotFoundError,
    SessionPersistError,
    SessionWriterConflictError,
)
from .events import ALLOWED_EVENT_TYPES, SCHEMA_VERSION, Event, EventType
from .manager import (
    PromptBuilderFactory,
    SessionManager,
    TitleGenerator,
)
from .metadata import SessionMetadata, SessionMetadataStore
from .model_context import (
    CalendarState,
    ModelContextPlan,
    ModelContextSnapshot,
    SessionModelContext,
)
from .model_history import (
    HistoryRelation,
    HistoryUpdate,
    PreparedModelHistory,
    SessionModelHistory,
)
from .query import (
    SessionActivity,
    SessionQuerySnapshot,
    list_session_activity,
    load_session,
    query_session,
)
from .recovery import (
    ReconciliationReport,
    RecoveredToolResult,
    RecoveryProbe,
    RecoveryProbeOutcome,
    ToolRecoveryAdapter,
    ToolTransactionReconciler,
)
from .rollout import (
    RolloutCommitState,
    RolloutRestoreResult,
    SessionRolloutRecorder,
    restore_rollout,
)
from .search import (
    SearchTextSegment,
    SessionMessageSearchResult,
    SessionSearchResponse,
    SessionSearchService,
    SessionTitleSearchResult,
)
from .session import Session
from .store import (
    JsonlSessionStore,
    NotifyingSessionStore,
    NullSessionStore,
    SessionStore,
    SessionSummary,
    SessionWriterLease,
)
from .tool_execution_evidence import (
    ToolExecutionEvidence,
    ToolExecutionEvidenceError,
    ToolExecutionEvidenceStore,
    render_interrupted_evidence,
    render_recoverable_evidence,
)

__all__ = [
    "ALLOWED_EVENT_TYPES",
    "SCHEMA_VERSION",
    "CacheEnvelopeState",
    "CacheEpochUpdate",
    "CalendarState",
    "Event",
    "EventType",
    "HistoryRelation",
    "HistoryUpdate",
    "JsonlSessionStore",
    "ModelContextPlan",
    "ModelContextSnapshot",
    "NotifyingSessionStore",
    "NullSessionStore",
    "PreparedCacheEpoch",
    "PreparedModelHistory",
    "PromptBuilderFactory",
    "ReconciliationReport",
    "RecoveredToolResult",
    "RecoveryProbe",
    "RecoveryProbeOutcome",
    "RolloutCommitState",
    "RolloutRestoreResult",
    "SearchTextSegment",
    "Session",
    "SessionActivity",
    "SessionCacheEpoch",
    "SessionCatalog",
    "SessionChange",
    "SessionChangeBroker",
    "SessionCorruptError",
    "SessionDescriptor",
    "SessionError",
    "SessionManager",
    "SessionMessageSearchResult",
    "SessionMetadata",
    "SessionMetadataStore",
    "SessionModelContext",
    "SessionModelHistory",
    "SessionMutationContext",
    "SessionNotFoundError",
    "SessionPersistError",
    "SessionQuerySnapshot",
    "SessionResolution",
    "SessionRevisionStore",
    "SessionRolloutRecorder",
    "SessionSearchResponse",
    "SessionSearchService",
    "SessionStore",
    "SessionSummary",
    "SessionTitleSearchResult",
    "SessionWriterConflictError",
    "SessionWriterLease",
    "TitleGenerator",
    "ToolExecutionEvidence",
    "ToolExecutionEvidenceError",
    "ToolExecutionEvidenceStore",
    "ToolRecoveryAdapter",
    "ToolTransactionReconciler",
    "current_session_mutation_context",
    "list_session_activity",
    "load_session",
    "normalize_session_title",
    "query_session",
    "render_interrupted_evidence",
    "render_recoverable_evidence",
    "restore_rollout",
    "session_mutation_context",
]

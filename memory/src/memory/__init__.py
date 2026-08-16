"""agent-friend · 长期记忆系统。

从 001 阶段 2 预留的占位演进为可用实现（需求 008）：

- **写路径**：``Memory.observe(fragment)`` 非阻塞入队 → 异步 LLM 抽取出
  episodic 摘要 + semantic 事实，落 SQLite。
- **读路径**：``Memory.retrieve(query, ...)`` 召回 pinned + 相关记忆，渲染成
  :class:`MemoryContext`，由 ``agent.Conversation`` 注入对话上下文。

模块边界：``memory`` 只认识 :class:`ConversationFragment` / :class:`Utterance`
（由 ``agent.memory_feed`` 投影产出），**运行时不依赖 ``agent``**。

详见 docs/requirements/008-engine-memory/design.md。
"""

from __future__ import annotations

from .contracts import (
    DEFAULT_OWNER_USER_ID,
    ConversationFragment,
    GateDecision,
    GateMode,
    Layer,
    MemoryContext,
    MemoryItem,
    RecallTrace,
    RecallTraceItem,
    Speaker,
    Utterance,
)
from .core_profile import (
    CORE_FIELD_SPECS,
    CoreFieldKey,
    CoreFieldSpec,
    CoreProfile,
    CoreProfileEntry,
    CoreProfileError,
    CoreProfileRenderer,
    CoreScopeKind,
)
from .extraction import (
    AsyncExtractionWorker,
    CoreUpdate,
    ExtractionAttempt,
    ExtractionContext,
    ExtractionContextBuilder,
    ExtractionOutput,
    ExtractionResult,
    Extractor,
    Reconciler,
    SemanticChange,
    SemanticFactDraft,
    SemanticOp,
    SemanticRetirement,
)
from .facade import Memory
from .factory import build_memory
from .retrieval import (
    Candidate,
    KeywordRetrieval,
    Renderer,
    RetrievalStrategy,
    ScoreWeights,
)
from .search import (
    AnalyzedQuery,
    DenseIndexState,
    EmbeddingIndexManager,
    EmbeddingProvider,
    EvidenceCandidate,
    EvidenceExcerpt,
    EvidenceSource,
    MemorySearchService,
    OnnxEmbeddingProvider,
    QueryAnalyzer,
    SearchRequest,
    SearchResult,
    SqliteVecIndex,
    StructuredMemorySource,
    TimeRange,
    VectorIndex,
)
from .store import (
    EpisodicRow,
    SemanticChangeMemberRow,
    SemanticChangeRow,
    SemanticRow,
    SqliteMemoryStore,
)

__version__ = "0.2.0"

__all__ = [
    "CORE_FIELD_SPECS",
    "DEFAULT_OWNER_USER_ID",
    "AnalyzedQuery",
    "AsyncExtractionWorker",
    "Candidate",
    "ConversationFragment",
    "CoreFieldKey",
    "CoreFieldSpec",
    "CoreProfile",
    "CoreProfileEntry",
    "CoreProfileError",
    "CoreProfileRenderer",
    "CoreScopeKind",
    "CoreUpdate",
    "DenseIndexState",
    "EmbeddingIndexManager",
    "EmbeddingProvider",
    "EpisodicRow",
    "EvidenceCandidate",
    "EvidenceExcerpt",
    "EvidenceSource",
    "ExtractionAttempt",
    "ExtractionContext",
    "ExtractionContextBuilder",
    "ExtractionOutput",
    "ExtractionResult",
    "Extractor",
    "GateDecision",
    "GateMode",
    "KeywordRetrieval",
    "Layer",
    "Memory",
    "MemoryContext",
    "MemoryItem",
    "MemorySearchService",
    "OnnxEmbeddingProvider",
    "QueryAnalyzer",
    "RecallTrace",
    "RecallTraceItem",
    "Reconciler",
    "Renderer",
    "RetrievalStrategy",
    "ScoreWeights",
    "SearchRequest",
    "SearchResult",
    "SemanticChange",
    "SemanticChangeMemberRow",
    "SemanticChangeRow",
    "SemanticFactDraft",
    "SemanticOp",
    "SemanticRetirement",
    "SemanticRow",
    "Speaker",
    "SqliteMemoryStore",
    "SqliteVecIndex",
    "StructuredMemorySource",
    "TimeRange",
    "Utterance",
    "VectorIndex",
    "build_memory",
]

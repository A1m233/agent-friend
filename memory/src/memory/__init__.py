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
from .extraction import (
    AsyncExtractionWorker,
    ExtractionOutput,
    ExtractionResult,
    Extractor,
    Reconciler,
    SemanticOp,
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
from .store import EpisodicRow, SemanticRow, SqliteMemoryStore

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_OWNER_USER_ID",
    "AsyncExtractionWorker",
    "Candidate",
    "ConversationFragment",
    "EpisodicRow",
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
    "RecallTrace",
    "RecallTraceItem",
    "Reconciler",
    "Renderer",
    "RetrievalStrategy",
    "ScoreWeights",
    "SemanticOp",
    "SemanticRow",
    "Speaker",
    "SqliteMemoryStore",
    "Utterance",
    "build_memory",
]

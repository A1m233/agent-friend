"""统一记忆搜索契约与编排。"""

from .analyzer import QueryAnalyzer
from .contracts import (
    AnalyzedQuery,
    CandidateDecision,
    EvidenceCandidate,
    EvidenceExcerpt,
    EvidenceKind,
    EvidenceSource,
    SaidBy,
    SearchIntent,
    SearchMode,
    SearchRequest,
    SearchResult,
    SourceFailure,
    SourceRun,
    TimeRange,
)
from .service import MemorySearchService
from .structured_source import StructuredMemorySource
from .time_parser import Bias, parse_time_expression
from .vector import (
    DenseIndexState,
    EmbeddingIndexManager,
    EmbeddingProvider,
    OnnxEmbeddingProvider,
    SqliteVecIndex,
    VectorFilters,
    VectorHit,
    VectorIndex,
    VectorRecord,
)

__all__ = [
    "AnalyzedQuery",
    "Bias",
    "CandidateDecision",
    "DenseIndexState",
    "EmbeddingIndexManager",
    "EmbeddingProvider",
    "EvidenceCandidate",
    "EvidenceExcerpt",
    "EvidenceKind",
    "EvidenceSource",
    "MemorySearchService",
    "OnnxEmbeddingProvider",
    "QueryAnalyzer",
    "SaidBy",
    "SearchIntent",
    "SearchMode",
    "SearchRequest",
    "SearchResult",
    "SourceFailure",
    "SourceRun",
    "SqliteVecIndex",
    "StructuredMemorySource",
    "TimeRange",
    "VectorFilters",
    "VectorHit",
    "VectorIndex",
    "VectorRecord",
    "parse_time_expression",
]

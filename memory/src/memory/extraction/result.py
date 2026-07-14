"""抽取管线的数据结构：LLM 输出（解析后）与落库结果（可观测）。

详见 docs/requirements/008-engine-memory/design.md §5.2 / §8。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..store import MemoryClass

__all__ = [
    "ALLOWED_FACT_KEYS",
    "ExtractionAttempt",
    "ExtractionAttemptStatus",
    "ExtractionOutput",
    "ExtractionResult",
    "SemanticOp",
    "normalize_memory_metadata",
]

OpKind = Literal["add", "supersede"]
ExtractionAttemptStatus = Literal[
    "success",
    "empty_response",
    "invalid_json",
    "invalid_shape",
    "empty_output",
    "llm_error",
]
ALLOWED_FACT_KEYS = frozenset({"user.name", "agent.name", "user.employer", "user.residence"})
_CORE_FACT_KEYS = frozenset({"user.name", "agent.name"})
_CORE_RELATION_RE = re.compile(
    r"用户(?:的)?(?:伴侣|妻子|丈夫|男朋友|女朋友|父亲|母亲|爸爸|妈妈|儿子|女儿|孩子)"
)
_FACT_KEY_PATTERNS = {
    "user.name": re.compile(r"用户(?:的)?(?:名字|姓名)(?:是|叫|为)|用户(?:叫|名叫)"),
    "agent.name": re.compile(
        r"(?:AI|助手|智能体)(?:的)?(?:名字|名称)(?:是|叫|为)|(?:AI|助手)(?:叫|名叫)"
    ),
    "user.employer": re.compile(r"用户(?:目前|现在)?(?:在.{1,24}工作|就职于|任职于)"),
    "user.residence": re.compile(r"用户(?:目前|现在)?(?:住在|居住在|定居在|家住)"),
}


@dataclass(frozen=True)
class SemanticOp:
    """LLM 提议的一次语义记忆操作。

    Attributes:
        op: ``add`` 新增 / ``supersede`` 取代某条旧事实。
        statement: 第三人称、原子化的事实陈述。
        importance: LLM 给的重要性初值 [0,1]。
        pinned: 是否"每次必进上下文"（仅姓名 / 核心关系等稳定身份事实置真）。
        speaker_origin: 该事实主要基于谁的话（``user`` / ``agent``）；
            ``user`` 在落库时加一档重要性。
        target_hint: ``supersede`` 时指向被取代旧事实的近似陈述，供 reconcile 定位。
    """

    op: OpKind
    statement: str
    importance: float = 0.5
    pinned: bool = False
    speaker_origin: str = "user"
    target_hint: str | None = None
    memory_class: MemoryClass = "normal"
    fact_key: str | None = None


def normalize_memory_metadata(
    statement: str,
    *,
    pinned: bool,
    memory_class: object,
    fact_key: object,
) -> tuple[MemoryClass, str | None, bool]:
    """对白名单字段做最后一道确定性校验，LLM 不能任意升级 core。"""
    requested_key = fact_key.strip() if isinstance(fact_key, str) else None
    normalized_key = (
        requested_key
        if requested_key in ALLOWED_FACT_KEYS
        and _FACT_KEY_PATTERNS[requested_key].search(statement)
        else _infer_fact_key(statement)
    )
    normalized_class: MemoryClass = (
        memory_class if memory_class in ("core", "priority", "normal") else "normal"
    )

    core_allowed = normalized_key in _CORE_FACT_KEYS or bool(_CORE_RELATION_RE.search(statement))
    if normalized_key in _CORE_FACT_KEYS:
        normalized_class = "core"
    elif normalized_key is not None and normalized_class == "normal":
        normalized_class = "priority"
    if normalized_class == "core" and not core_allowed:
        normalized_class = "priority"
    if pinned and normalized_class == "normal":
        normalized_class = "core" if core_allowed else "priority"
    return normalized_class, normalized_key, normalized_class != "normal"


def _infer_fact_key(statement: str) -> str | None:
    for fact_key, pattern in _FACT_KEY_PATTERNS.items():
        if pattern.search(statement):
            return fact_key
    return None


@dataclass(frozen=True)
class ExtractionOutput:
    """对一个 fragment 抽取后、解析好的 LLM 产物。

    Attributes:
        episodic_entries: 这段对话发生了什么的具体记录列表（每条含原话里的具体词；
            issue 003 root cause——旧 prompt 把对话压成单条话题摘要导致下游召回失败）。
            空表示没什么可记的。
        semantic_ops: 提议落库的语义事实操作。
    """

    episodic_entries: list[str] = field(default_factory=list)
    semantic_ops: list[SemanticOp] = field(default_factory=list)

    def is_noop(self) -> bool:
        """既没有可记的情节条目、也没有语义操作。"""
        return not self.episodic_entries and not self.semantic_ops


@dataclass(frozen=True)
class ExtractionAttempt:
    """一次 LLM 抽取尝试的诊断快照。

    仅通过显式 observer 暴露；生产默认不持久化原始响应。评测 harness 可用它
    区分 API 异常、解析失败、合法空产出与正常产出，并按 fragment checkpoint。
    """

    session_id: str
    first_source_ref: str | None
    last_source_ref: str | None
    utterance_count: int
    existing_fact_count: int
    status: ExtractionAttemptStatus
    duration_ms: float
    episodic_count: int = 0
    semantic_count: int = 0
    raw_response: str | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    """一次抽取真正落库的结果，供 observability（design §8）。"""

    session_id: str
    episodic_ids: list[str] = field(default_factory=list)
    added_semantic: list[str] = field(default_factory=list)  # "statement" 文本
    superseded_semantic: list[str] = field(default_factory=list)  # 旧 statement 文本

    def is_empty(self) -> bool:
        return not self.episodic_ids and not self.added_semantic and not self.superseded_semantic

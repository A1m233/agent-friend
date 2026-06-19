"""记忆模块的公共契约类型。

这些类型构成 ``memory`` 与外部（主要是 ``agent``）的边界：

- **写路径输入**：:class:`ConversationFragment` / :class:`Utterance`
  —— 由 ``agent`` 侧的投影层（``agent.memory_feed``）把会话事件投影成"干净的对话
  素材"后交给 :meth:`memory.Memory.observe`。**memory 只认识这些，不认识
  ``agent.sessions.Event``。**
- **读路径输出**：:class:`MemoryContext` / :class:`MemoryItem`
  —— :meth:`memory.Memory.retrieve` 的返回，``rendered`` 可直接注入 system 段，
  ``items`` 供 observability。

详见 docs/requirements/008-engine-memory/design.md §3。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

__all__ = [
    "DEFAULT_OWNER_USER_ID",
    "ConversationFragment",
    "GateDecision",
    "GateMode",
    "Layer",
    "MemoryContext",
    "MemoryItem",
    "RecallTrace",
    "RecallTraceItem",
    "Speaker",
    "Utterance",
]

DEFAULT_OWNER_USER_ID = "local"
"""单 user 假设下的固定 owner id。schema 已留 ``owner_user_id`` 列，v1 锁此值。"""

Speaker = Literal["user", "agent"]
"""发言者来源。``user`` 的写入权重高一档（design §5.3）。"""

Layer = Literal["episodic", "semantic", "pinned"]
"""召回条目所属记忆层。``pinned`` 是 ``semantic`` 中 ``pinned=1`` 的子集，
召回展示时单列一层便于观测。"""

GateMode = Literal["strict", "lenient"]
"""pinned relevance gate 的阈值档位。

- ``strict``：完全走 FTS5 命中判定。
- ``lenient``：短 query（< 6 字）直接全部通过；长 query 走 FTS5 命中判定。
"""

GateDecision = Literal["disabled", "pass-through", "matched"]
"""一次 retrieve 中 pinned gate 的决策类别。

- ``disabled``：gate 被关闭（hot-fix 或显式禁用），pinned 全量注入。
- ``pass-through``：空 query 或 lenient 模式下短 query，pinned 全量通过。
- ``matched``：通过 FTS5 相关性判定后过滤的 pinned。
"""


@dataclass(frozen=True)
class Utterance:
    """一条"干净的对话发言"——记忆抽取的最小素材单元。

    Attributes:
        speaker: 发言者（user / agent）。
        text: 发言正文（已剥离工具噪声等，见 ``agent.memory_feed`` 的过滤策略）。
        ts: 发言时间。
        source_ref: 溯源指针，形如 ``"{session_id}#{event_uuid}"``。
            v1 只存不取（design §1.2），future backfill / 回取按它定位原文。
    """

    speaker: Speaker
    text: str
    ts: datetime
    source_ref: str


@dataclass(frozen=True)
class ConversationFragment:
    """一段待抽取的对话素材（v1 = 一轮 user+assistant）。

    由 ``agent.memory_feed`` 投影产出，交给 :meth:`memory.Memory.observe`。

    Attributes:
        session_id: 来源会话 id。
        utterances: 本段发言（按时间序）。
        persona_id: 本段归属的 persona（episodic 按 persona 隔离的预留维度）。
        owner_user_id: 多 user 预留维度，v1 固定为 :data:`DEFAULT_OWNER_USER_ID`。
    """

    session_id: str
    utterances: list[Utterance]
    persona_id: str
    owner_user_id: str = DEFAULT_OWNER_USER_ID

    def is_empty(self) -> bool:
        """没有任何可抽取发言时为真（worker 可直接跳过）。"""
        return not self.utterances


@dataclass(frozen=True)
class MemoryItem:
    """一条被召回的记忆（结构化，供 observability / 调试）。

    Attributes:
        text: 记忆正文（semantic 的 statement / episodic 的 summary）。
        layer: 所属层，:data:`Layer` 之一。
        source_ref: 溯源指针（semantic_id / episodic_id / session 指针）。
        score: 召回综合得分（排序展示用；pinned 恒定高位）。
    """

    text: str
    layer: Layer
    source_ref: str
    score: float


@dataclass(frozen=True)
class MemoryContext:
    """:meth:`memory.Memory.retrieve` 的返回。

    Attributes:
        rendered: 渲染好的整段记忆文本，可直接作为一条 system 内容注入；
            **空召回时为空串**。pinned 与召回结果的内部排布由 renderer 编排，
            对外是单一整体（design §6.1）。
        items: 结构化的每条召回记忆，空召回时为空列表。
    """

    rendered: str
    items: list[MemoryItem]

    def is_empty(self) -> bool:
        """无可注入内容时为真（调用方据此决定是否注入记忆段）。"""
        return not self.rendered

    @classmethod
    def empty(cls) -> MemoryContext:
        """空召回的便捷构造。"""
        return cls(rendered="", items=[])


@dataclass(frozen=True)
class RecallTraceItem:
    """召回 trace 视角下的一条命中（结构与 MemoryItem 等价，单列出来允许
    扩展 trace-specific 字段而不污染 MemoryItem）。

    Attributes:
        text: 记忆正文（semantic 的 statement / episodic 的 summary）。
        layer: 所属层，:data:`Layer` 之一。
        source_ref: 溯源指针（semantic_id / episodic_id / session 指针）。
        score: 召回综合得分（pinned 恒定高位）。
    """

    text: str
    layer: Layer
    source_ref: str
    score: float


@dataclass(frozen=True)
class RecallTrace:
    """一次 retrieve 的完整 trace（observability + inspector 用）。

    Attributes:
        timestamp: 触发时刻（UTC）。
        query: 入参 query 文本（不脱敏，dev 内部用）。
        owner_user_id: 入参 owner。
        persona_id: 入参 persona。
        top_k: 入参 top_k。
        source: ``natural``（agent 自然召回触发）/ ``probe``（inspector 试探）。
        pinned_pre_gate: gate 前 pinned 条目数。
        pinned_post_gate: gate 后 pinned 条目数。
        gate_enabled: pinned_gate 是否启用。
        gate_mode: gate 档位（strict / lenient），gate 关闭时为 ``None``。
        gate_decision: gate 决策类别（disabled / pass-through / matched）。
        candidates_count: KeywordRetrieval 返回候选数。
        ranked_count: rank 后截断到 top_k 实际数。
        items: 最终注入的条目（pinned + recalled 合并后的 MemoryContext.items）。
    """

    timestamp: datetime
    query: str
    owner_user_id: str
    persona_id: str
    top_k: int
    source: Literal["natural", "probe"]
    pinned_pre_gate: int
    pinned_post_gate: int
    gate_enabled: bool
    gate_mode: GateMode | None
    gate_decision: GateDecision
    candidates_count: int
    ranked_count: int
    items: list[RecallTraceItem]

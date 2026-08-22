"""评测数据的统一契约——与具体基准格式解耦的"中间表示"。

各基准（LoCoMo / 未来 LongMemEval 等）的原始 JSON 由各自的 loader 归一化成这里的
:class:`EvalCase`，下游 adapter / harness 只认识这套类型，新增基准 = 新增一个 loader。

这一层是**纯数据**，不依赖 ``memory``——角色映射（说话人 → user/agent）等耦合 memory
契约的转换放在 ``adapters`` 层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EvalTurn:
    """一条对话发言（已归一化）。

    Attributes:
        speaker: 原始说话人名（如 LoCoMo 的 ``speaker_a`` / ``speaker_b`` 对应的人名）。
            角色到 user/agent 的映射由 adapter 决定，这里只保留原始名。
        text: 发言正文。
        ts: 发言时间（取所属 session 的时间戳；同一 session 内共享）。
        dia_id: 原始对话 id（如 LoCoMo 的 ``D1:1``），供 evidence 溯源。
        session_index: 所属 session 的序号（chronological）。
    """

    speaker: str
    text: str
    ts: datetime
    dia_id: str
    session_index: int


@dataclass(frozen=True)
class EvalQuestion:
    """一道评测问题及其 ground truth。

    Attributes:
        question: 问题文本。
        answer: 标准答案（原始可能是数字等，统一为字符串；缺失为空串）。
        category: 类别**标签**（已归一为人类可读字符串，跨基准通用）。LoCoMo 把数字码
            映射成 ``multi-hop`` / ``temporal`` / ``open-domain`` / ``single-hop`` /
            ``adversarial``；PerLTQA 用 ``dialogues`` 等。缺失为 ``None``。
        evidence: 支撑答案的来源 id 列表（LoCoMo 的对话 id / PerLTQA 的 memory 索引），
            缺失为空列表。
        anchors: 答案关键 token 列表（PerLTQA 的 ``Memory Anchors`` token，丢弃字符 span）。
            仅 PerLTQA 填充，其它基准默认空列表。供 anchor 召回判分器使用。
        expected_memory_layer: 产品切片可声明 anchor 必须出现在哪个记忆层；普通基准为空。
        expected_core_field: Core 产品切片可进一步约束必须由哪个 Core 字段承载。
        forbidden_memory_layers: 出现等价 anchor 即判为分层违规的记忆层。
        forbidden_anchors: 召回内容中不得出现的旧事实 anchor；用于纠错后旧事实仍活跃的回归。
    """

    question: str
    answer: str
    category: str | None
    evidence: list[str]
    anchors: list[str] = field(default_factory=list)
    expected_memory_layer: str | None = None
    expected_core_field: str | None = None
    forbidden_memory_layers: list[str] = field(default_factory=list)
    forbidden_anchors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvalCase:
    """一个评测样本 = 一段长对话 + 配套问答。

    Attributes:
        sample_id: 样本标识（如 LoCoMo 的 ``conv-26``）。
        speaker_a: 第一位说话人名（adapter 默认映射为 user）。
        speaker_b: 第二位说话人名（adapter 默认映射为 agent）。
        turns: 全部发言，按时间序。
        questions: 配套问答。
        semantic_update_context_turns: 大于 0 时，adapter 给后续 fragment 附加此前完整
            user/agent turns 的冻结 Semantic update context；普通基准默认关闭。
    """

    sample_id: str
    speaker_a: str
    speaker_b: str
    turns: list[EvalTurn]
    questions: list[EvalQuestion]
    semantic_update_context_turns: int = 0

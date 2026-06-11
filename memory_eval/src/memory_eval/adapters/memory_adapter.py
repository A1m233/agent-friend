"""把统一 :class:`EvalCase` 接到 ``memory`` 的公共接口（写 ``observe`` / 读 ``retrieve``）。

适配取舍（PoC，均为可演进的局部决策）：

- **角色映射**：LoCoMo 是两人对话（``speaker_a`` / ``speaker_b``），而 ``memory`` 契约只有
  ``user`` / ``agent`` 两个角色。这里固定把 ``speaker_a`` 映射成 ``user``、``speaker_b``
  映射成 ``agent``——只为喂进抽取器，抽取会从两边发言里提取事实，映射不影响"记住了什么"。
- **fragment 粒度**：一个 session 投影成一个 :class:`ConversationFragment`
  （utterances = 该 session 全部 turns），而非严格"一轮 user+agent"。抽取 prompt 吃多轮
  对话素材即可，这样每个 conversation 的 LLM 抽取调用次数 ≈ session 数，成本可控。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from memory import ConversationFragment, Utterance

if TYPE_CHECKING:
    from memory import Memory, MemoryContext, Speaker
    from memory_eval.datasets import EvalCase, EvalTurn

__all__ = [
    "BENCHMARK_PERSONA_ID",
    "ingest_case",
    "retrieve_for_question",
]

BENCHMARK_PERSONA_ID = "benchmark"
"""评测固定使用的 persona id（memory 的 episodic 按 persona 隔离，评测只用一个）。"""


def _role(speaker: str, speaker_a: str) -> Speaker:
    return "user" if speaker == speaker_a else "agent"


def ingest_case(memory: Memory, case: EvalCase) -> None:
    """把一个 case 的全部 turns 按 session 投影成 fragment 灌入，**阻塞到抽取完成**。

    抽取是异步的（``observe`` 非阻塞入队），评测需要"全部记忆落库后再召回"，故在末尾
    ``flush()`` 等待抽取队列清空。
    """
    by_session: dict[int, list[EvalTurn]] = {}
    for turn in case.turns:
        by_session.setdefault(turn.session_index, []).append(turn)

    for idx in sorted(by_session):
        utterances = [
            Utterance(
                speaker=_role(turn.speaker, case.speaker_a),
                text=turn.text,
                ts=turn.ts,
                source_ref=f"{case.sample_id}#{turn.dia_id}",
            )
            for turn in by_session[idx]
        ]
        if not utterances:
            continue
        memory.observe(
            ConversationFragment(
                session_id=case.sample_id,
                utterances=utterances,
                persona_id=BENCHMARK_PERSONA_ID,
            )
        )

    memory.flush()


def retrieve_for_question(memory: Memory, question: str) -> MemoryContext:
    """用问题走召回，返回可注入的 :class:`MemoryContext`（含结构化 ``items``）。"""
    return memory.retrieve(question, persona_id=BENCHMARK_PERSONA_ID)

"""adapter：EvalCase 灌入 observe → flush → retrieve 召回（fake LLM + 临时 SQLite）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from memory_eval.adapters import ingest_case, retrieve_for_question
from memory_eval.datasets import EvalCase, EvalQuestion, EvalTurn

from llm_providers import ModelRequest, ModelResponse
from memory import build_memory


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=self.reply)


def _case() -> EvalCase:
    now = datetime.now(UTC)
    return EvalCase(
        sample_id="c1",
        speaker_a="Alice",
        speaker_b="Bob",
        turns=[
            EvalTurn(
                speaker="Alice",
                text="我养了一只猫叫Tom",
                ts=now,
                dia_id="D1:1",
                session_index=1,
            ),
            EvalTurn(
                speaker="Bob",
                text="好可爱",
                ts=now,
                dia_id="D1:2",
                session_index=1,
            ),
        ],
        questions=[
            EvalQuestion(question="Tom", answer="猫", category="dialogues", evidence=["D1:1"])
        ],
    )


def test_ingest_then_retrieve_recalls(tmp_path: Path) -> None:
    reply = '{"semantic_changes": [{"retirements": [], "new_facts": [{"statement": "用户养了一只叫Tom的猫", "reason": "test fixture"}]}], "core_updates": [], "episodic_entries": []}'
    memory = build_memory(tmp_path / "m.db", FakeLLM(reply))  # type: ignore[arg-type]
    try:
        ingest_case(memory, _case())
        ctx = retrieve_for_question(memory, "还记得Tom吗")
        assert not ctx.is_empty()
        assert "Tom" in ctx.rendered
    finally:
        memory.close()


def test_retrieve_empty_when_no_match(tmp_path: Path) -> None:
    reply = '{"semantic_changes": [], "core_updates": [], "episodic_entries": []}'
    memory = build_memory(tmp_path / "m.db", FakeLLM(reply))  # type: ignore[arg-type]
    try:
        ingest_case(memory, _case())
        ctx = retrieve_for_question(memory, "完全不相关的查询内容")
        assert ctx.is_empty()
    finally:
        memory.close()

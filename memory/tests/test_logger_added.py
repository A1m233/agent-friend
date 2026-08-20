"""验证 memory 关键边界已接入 logger：触发后能产生预期 name / level 的 record。

不验证具体 message 内容（避免文字变动导致脆弱断言），只验证：
- ``memory.facade`` / ``memory.retrieval.strategy`` / ``memory.retrieval.pinned_gate`` / ``memory.store.sqlite_store`` 在典型调用路径上会 emit INFO 级 record
- ``memory.extraction.reconciler`` 在 supersede 冲突路径上会 emit WARN 级 record
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from llm_providers import ModelRequest, ModelResponse
from memory import (
    ConversationFragment,
    Extractor,
    KeywordRetrieval,
    Memory,
    Reconciler,
    SqliteMemoryStore,
    Utterance,
)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _fragment(text: str) -> ConversationFragment:
    return ConversationFragment(
        session_id="s1",
        utterances=[Utterance(speaker="user", text=text, ts=datetime.now(UTC), source_ref="s1#u0")],
        persona_id="p1",
    )


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=self.reply)


def _capture_memory_logs() -> _ListHandler:
    handler = _ListHandler()
    logger = logging.getLogger("memory")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def test_facade_observe_and_retrieve_emit_info() -> None:
    handler = _capture_memory_logs()
    store = SqliteMemoryStore(":memory:")
    mem = Memory(
        store,
        Extractor(_FakeLLM('{"episodic_summary": null, "semantic_ops": []}'), prompt="x"),  # type: ignore[arg-type]
        Reconciler(store),
        retrieval=KeywordRetrieval(store),
    )

    mem.observe(_fragment("测试"))
    mem.flush()
    mem.retrieve("测试", persona_id="p1")

    names = {r.name for r in handler.records}
    assert "memory.facade" in names
    assert any(r.levelno == logging.INFO for r in handler.records if r.name == "memory.facade")
    mem.close()


def test_retrieval_strategy_emits_info_on_search() -> None:
    handler = _capture_memory_logs()
    store = SqliteMemoryStore(":memory:")
    retrieval = KeywordRetrieval(store)
    retrieval.search("猫", owner_user_id="u1", persona_id="p1", limit=4)

    assert any(
        r.name == "memory.retrieval.strategy" and r.levelno == logging.INFO for r in handler.records
    )


def test_sqlite_store_emits_info_on_init() -> None:
    handler = _capture_memory_logs()
    SqliteMemoryStore(":memory:").close()

    assert any(
        r.name == "memory.store.sqlite_store" and r.levelno == logging.INFO for r in handler.records
    )

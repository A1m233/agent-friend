"""026 · RecallBuffer 单测。"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Thread

from agent_bridge.dev.recall_buffer import RecallBuffer
from memory.contracts import RecallTrace, RecallTraceItem


def _trace(source: str, query: str) -> RecallTrace:
    return RecallTrace(
        timestamp=datetime.now(UTC),
        query=query,
        owner_user_id="local",
        persona_id="p1",
        top_k=8,
        source=source,  # type: ignore[arg-type]
        pinned_pre_gate=0,
        pinned_post_gate=0,
        gate_enabled=True,
        gate_mode="lenient",
        gate_decision="pass-through",
        candidates_count=1,
        ranked_count=1,
        items=[RecallTraceItem(text="x", layer="semantic", source_ref="s1", score=1.0)],
    )


def test_record_and_snapshot_reverse_order() -> None:
    buf = RecallBuffer()
    buf.record(_trace("natural", "a"))
    buf.record(_trace("natural", "b"))
    snapshot = buf.snapshot()
    assert [t.query for t in snapshot] == ["b", "a"]


def test_maxlen_evicts_oldest() -> None:
    buf = RecallBuffer(capacity=3)
    for i in range(5):
        buf.record(_trace("natural", str(i)))
    snapshot = buf.snapshot()
    assert [t.query for t in snapshot] == ["4", "3", "2"]


def test_snapshot_limit() -> None:
    buf = RecallBuffer()
    for i in range(5):
        buf.record(_trace("natural", str(i)))
    assert [t.query for t in buf.snapshot(limit=2)] == ["4", "3"]


def test_clear() -> None:
    buf = RecallBuffer()
    buf.record(_trace("natural", "a"))
    buf.clear()
    assert buf.snapshot() == []


def test_thread_safety_no_crash() -> None:
    """并发 record + snapshot 不抛异常。"""
    buf = RecallBuffer(capacity=100)
    errors: list[Exception] = []

    def record_many() -> None:
        try:
            for i in range(500):
                buf.record(_trace("natural", f"t{i}"))
        except Exception as e:
            errors.append(e)

    def snapshot_many() -> None:
        try:
            for _ in range(500):
                buf.snapshot(limit=10)
        except Exception as e:
            errors.append(e)

    threads = [Thread(target=record_many), Thread(target=snapshot_many)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors

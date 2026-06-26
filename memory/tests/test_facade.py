"""``Memory`` 门面端到端：observe → 异步抽取落库 → retrieve 召回注入。

用 fake LLM（不触发真实调用）+ 真实 SQLite（:memory:）跑通写读闭环。
"""

from __future__ import annotations

from datetime import UTC, datetime

from memory.contracts import RecallTrace

from memory import (
    ConversationFragment,
    Extractor,
    KeywordRetrieval,
    Memory,
    Reconciler,
    SqliteMemoryStore,
    Utterance,
)


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, messages: list[dict[str, object]], **_kw: object) -> str:
        return self.reply


def _fragment(text: str) -> ConversationFragment:
    return ConversationFragment(
        session_id="s1",
        utterances=[Utterance(speaker="user", text=text, ts=datetime.now(UTC), source_ref="s1#u0")],
        persona_id="p1",
    )


def _memory(reply: str) -> Memory:
    store = SqliteMemoryStore(":memory:")
    return Memory(
        store,
        Extractor(FakeLLM(reply), prompt="x"),  # type: ignore[arg-type]
        Reconciler(store),
        retrieval=KeywordRetrieval(store),
    )


def test_observe_then_retrieve_recalls_fact() -> None:
    reply = '{"episodic_summary": null, "semantic_ops": [{"op": "add", "statement": "用户养了一只叫Tom的猫"}]}'
    mem = _memory(reply)
    mem.observe(_fragment("我养了一只猫叫Tom"))
    mem.flush()

    ctx = mem.retrieve("还记得Tom吗", persona_id="p1")
    assert not ctx.is_empty()
    assert "Tom" in ctx.rendered
    mem.close()


def test_pinned_dropped_when_query_unrelated_with_default_gate() -> None:
    """pass-1 M13.3：默认开 pinned relevance gate；闲聊 query 与 pinned 无 jieba
    共享词时，pinned **不再** 作为占位安慰品被注入（issue 003 主因 2）。

    pre-pass-1 行为是 "pinned 永远附带"；本期默认改为 gate 过滤。要恢复旧行为
    需显式 ``Memory(..., pinned_relevance_gate=False)``（切片 baseline 用），
    见 ``test_pinned_gate.py``。
    """
    reply = '{"episodic_summary": null, "semantic_ops": [{"op": "add", "statement": "用户叫小明", "pinned": true}]}'
    mem = _memory(reply)
    mem.observe(_fragment("我叫小明"))
    mem.flush()

    # 长闲聊 query 与 "用户叫小明" 无 jieba 共享词 → pinned 砍掉
    ctx = mem.retrieve("今天天气真好出去走走怎么样", persona_id="p1")
    assert "用户叫小明" not in ctx.rendered
    assert not any(i.layer == "pinned" for i in ctx.items)
    mem.close()


def test_retrieve_empty_when_nothing_relevant() -> None:
    reply = '{"episodic_summary": null, "semantic_ops": []}'
    mem = _memory(reply)
    mem.observe(_fragment("随便聊聊"))
    mem.flush()

    ctx = mem.retrieve("完全不相关的查询内容", persona_id="p1")
    assert ctx.is_empty()
    mem.close()


def test_close_is_idempotent() -> None:
    mem = _memory('{"episodic_summary": null, "semantic_ops": []}')
    mem.close()
    mem.close()


def test_warmup_does_not_write_memory_items() -> None:
    mem = _memory('{"episodic_summary": null, "semantic_ops": []}')
    mem.warmup()

    ctx = mem.retrieve("语音通话", persona_id="p1")
    assert ctx.is_empty()
    mem.close()


def test_on_retrieved_callback_receives_trace() -> None:
    """026: retrieve 完成后 on_retrieved 收到完整 RecallTrace，source 默认 natural。"""
    reply = '{"episodic_summary": null, "semantic_ops": [{"op": "add", "statement": "用户养了一只叫Tom的猫"}]}'
    mem = _memory(reply)
    mem.observe(_fragment("我养了一只猫叫Tom"))
    mem.flush()

    traces: list[RecallTrace] = []
    mem._on_retrieved = traces.append

    ctx = mem.retrieve("还记得Tom吗", persona_id="p1")
    assert len(traces) == 1
    trace = traces[0]
    assert trace.query == "还记得Tom吗"
    assert trace.persona_id == "p1"
    assert trace.owner_user_id == "local"
    assert trace.source == "natural"
    assert trace.gate_enabled is True
    assert trace.gate_mode == "lenient"
    assert trace.items == [
        type(trace.items[0])(text=i.text, layer=i.layer, source_ref=i.source_ref, score=i.score)
        for i in ctx.items
    ]
    mem.close()


def test_retrieve_source_kwarg_passed_to_trace() -> None:
    """026: retrieve 的 source kwarg 透传到 trace.source。"""
    mem = _memory('{"episodic_summary": null, "semantic_ops": []}')
    traces: list[RecallTrace] = []
    mem._on_retrieved = traces.append

    mem.retrieve("query", persona_id="p1", source="probe")
    assert traces[0].source == "probe"
    mem.close()


def test_on_retrieved_exception_does_not_break_retrieve() -> None:
    """026: on_retrieved 抛异常不应让 retrieve 失败。"""
    reply = '{"episodic_summary": null, "semantic_ops": [{"op": "add", "statement": "用户养了一只叫Tom的猫"}]}'
    mem = _memory(reply)
    mem.observe(_fragment("我养了一只猫叫Tom"))
    mem.flush()

    def _boom(_t: RecallTrace) -> None:
        raise RuntimeError("boom")

    mem._on_retrieved = _boom
    ctx = mem.retrieve("还记得Tom吗", persona_id="p1")
    assert not ctx.is_empty()
    mem.close()

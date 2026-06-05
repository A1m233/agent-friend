"""``Memory`` 门面端到端：observe → 异步抽取落库 → retrieve 召回注入。

用 fake LLM（不触发真实调用）+ 真实 SQLite（:memory:）跑通写读闭环。
"""

from __future__ import annotations

from datetime import UTC, datetime

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


def test_pinned_always_injected_even_without_query_match() -> None:
    reply = '{"episodic_summary": null, "semantic_ops": [{"op": "add", "statement": "用户叫小明", "pinned": true}]}'
    mem = _memory(reply)
    mem.observe(_fragment("我叫小明"))
    mem.flush()

    # query 与 pinned 无关，pinned 仍应注入
    ctx = mem.retrieve("今天天气怎么样", persona_id="p1")
    assert "用户叫小明" in ctx.rendered
    assert any(i.layer == "pinned" for i in ctx.items)
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

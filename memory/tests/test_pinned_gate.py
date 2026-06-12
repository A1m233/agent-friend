"""pass-1 M13.3 pinned relevance gate 单测。

身份题 fixture（design §5.4）覆盖 R-4.2.x 的回归保护——用户问"我叫什么 /
我家人是谁"等显性身份提问时 pinned 必须命中；闲聊负样本时 pinned 不应被
作为占位安慰品注入。所有 PR 必跑（不烧 LLM token）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from memory.retrieval import pinned_gate

from memory import SemanticRow, SqliteMemoryStore


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_identity_pinned(store: SqliteMemoryStore) -> None:
    """种入一组身份级 pinned 事实，覆盖典型 R-4.2.x 场景。"""
    now = _now()
    rows = [
        SemanticRow(
            id="p-name",
            statement="用户名字是张小红",
            persona_id="p1",
            created_at=now,
            updated_at=now,
            pinned=True,
        ),
        SemanticRow(
            id="p-brother",
            statement="用户的弟弟叫张小明",
            persona_id="p1",
            created_at=now,
            updated_at=now,
            pinned=True,
        ),
        SemanticRow(
            id="p-pet",
            statement="用户养了一只叫Tom的猫",
            persona_id="p1",
            created_at=now,
            updated_at=now,
            pinned=True,
        ),
        SemanticRow(
            id="p-job",
            statement="用户是一名农业工程师",
            persona_id="p1",
            created_at=now,
            updated_at=now,
            pinned=True,
        ),
    ]
    for r in rows:
        store.add_semantic(r)


def _pinned_ids(rows: list[SemanticRow]) -> set[str]:
    return {r.id for r in rows}


# ===== lenient 档（默认）=====


def test_lenient_short_identity_query_passes_all_pinned() -> None:
    """极短身份提问（< 6 字）在 lenient 档下让 pinned 全部通过，避免 jieba 切词
    差异让"我叫什么 / 我是谁"挂掉（R-4.2.3 显性引用兜底）。"""
    store = SqliteMemoryStore(":memory:")
    try:
        _seed_identity_pinned(store)
        pinned = store.pinned(owner_user_id="local", persona_id="p1")

        for q in ("我叫什么", "我是谁", "我家小猫"):
            kept = pinned_gate(q, pinned, store=store, owner_user_id="local", mode="lenient")
            assert _pinned_ids(kept) == _pinned_ids(pinned), f"短 query {q!r} 应放行全部 pinned"
    finally:
        store.close()


def test_lenient_long_query_with_shared_word_keeps_matched_pinned() -> None:
    """长 query 走 FTS5；共享 jieba 词的 pinned 命中。"""
    store = SqliteMemoryStore(":memory:")
    try:
        _seed_identity_pinned(store)
        pinned = store.pinned(owner_user_id="local", persona_id="p1")

        # query "我家人都有什么名字" 切 [我家 / 人 / 都 / 有 / 什么 / 名字]
        # 共享 "名字" → p-name 命中；其他无共享
        kept = pinned_gate(
            "我家人都有什么名字",
            pinned,
            store=store,
            owner_user_id="local",
            mode="lenient",
        )
        assert _pinned_ids(kept) == {"p-name"}
    finally:
        store.close()


def test_lenient_long_chat_query_with_no_overlap_drops_all_pinned() -> None:
    """长闲聊 query 与所有 pinned 无 jieba 共享词，pinned 全部不注入
    （issue 003 主因 2 的核心收益：不让 pinned 作占位安慰品）。"""
    store = SqliteMemoryStore(":memory:")
    try:
        _seed_identity_pinned(store)
        pinned = store.pinned(owner_user_id="local", persona_id="p1")

        kept = pinned_gate(
            "今天天气真好出去走走怎么样",
            pinned,
            store=store,
            owner_user_id="local",
            mode="lenient",
        )
        assert kept == []
    finally:
        store.close()


def test_lenient_empty_query_passes_all_pinned() -> None:
    """空 / 纯空白 query 透传原 pinned（保持调用方对"无 query 时只查 pinned"的依赖）。"""
    store = SqliteMemoryStore(":memory:")
    try:
        _seed_identity_pinned(store)
        pinned = store.pinned(owner_user_id="local", persona_id="p1")

        for q in ("", "   ", "\n"):
            kept = pinned_gate(q, pinned, store=store, owner_user_id="local", mode="lenient")
            assert _pinned_ids(kept) == _pinned_ids(pinned)
    finally:
        store.close()


def test_lenient_empty_pinned_returns_empty() -> None:
    """无 pinned 时直接返回（不查 FTS5，避免无意义工作）。"""
    store = SqliteMemoryStore(":memory:")
    try:
        kept = pinned_gate("我叫什么", [], store=store, owner_user_id="local", mode="lenient")
        assert kept == []
    finally:
        store.close()


# ===== strict 档 =====


def test_strict_short_query_still_goes_through_fts() -> None:
    """strict 档下短 query 也走 FTS5，不像 lenient 那样直接放行。"""
    store = SqliteMemoryStore(":memory:")
    try:
        _seed_identity_pinned(store)
        pinned = store.pinned(owner_user_id="local", persona_id="p1")

        # "猫" 单字 query → jieba 切 ["猫"] → 命中 p-pet "用户养了一只叫Tom的猫"
        kept = pinned_gate("猫", pinned, store=store, owner_user_id="local", mode="strict")
        assert _pinned_ids(kept) == {"p-pet"}

        # "我家人" 切 [我家 / 人] → 无任何 pinned 共享 → strict 档全砍
        kept = pinned_gate("我家人", pinned, store=store, owner_user_id="local", mode="strict")
        assert kept == []
    finally:
        store.close()


def test_strict_long_chat_query_drops_all_pinned() -> None:
    """strict 档与 lenient 在长闲聊 query 上行为一致。"""
    store = SqliteMemoryStore(":memory:")
    try:
        _seed_identity_pinned(store)
        pinned = store.pinned(owner_user_id="local", persona_id="p1")

        kept = pinned_gate(
            "今天天气真好出去走走怎么样",
            pinned,
            store=store,
            owner_user_id="local",
            mode="strict",
        )
        assert kept == []
    finally:
        store.close()


# ===== facade 集成：Memory.retrieve 走 gate =====


def test_facade_retrieve_with_gate_drops_unrelated_pinned() -> None:
    """门面层 retrieve 默认开 gate；闲聊 query 不再让 pinned 全量注入。"""
    from memory.extraction import Extractor, Reconciler

    from memory import Memory

    class _FakeLLM:
        def complete(self, *_a: object, **_kw: object) -> str:
            return '{"episodic_entries": [], "semantic_ops": []}'

    store = SqliteMemoryStore(":memory:")
    try:
        _seed_identity_pinned(store)
        mem = Memory(
            store,
            Extractor(_FakeLLM(), prompt="x"),  # type: ignore[arg-type]
            Reconciler(store),
            # 默认 pinned_relevance_gate=True / mode='lenient'
        )
        try:
            ctx = mem.retrieve("今天天气真好出去走走怎么样", persona_id="p1")
            # gate 砍掉全部 pinned → MemoryContext 为空（无召回也无 pinned）
            assert ctx.is_empty()
        finally:
            mem.close()
    finally:
        # mem.close() 已关 store；再 close 是幂等
        pass


def test_facade_retrieve_without_gate_keeps_legacy_behavior() -> None:
    """关闭 gate 时退回 pre-pass-1 行为（pinned 全量注入），供切片 baseline 对照。"""
    from memory.extraction import Extractor, Reconciler

    from memory import Memory

    class _FakeLLM:
        def complete(self, *_a: object, **_kw: object) -> str:
            return '{"episodic_entries": [], "semantic_ops": []}'

    store = SqliteMemoryStore(":memory:")
    try:
        _seed_identity_pinned(store)
        mem = Memory(
            store,
            Extractor(_FakeLLM(), prompt="x"),  # type: ignore[arg-type]
            Reconciler(store),
            pinned_relevance_gate=False,
        )
        try:
            ctx = mem.retrieve("今天天气真好出去走走怎么样", persona_id="p1")
            # 关闭 gate → 4 条 pinned 全部注入
            assert not ctx.is_empty()
            pinned_items = [it for it in ctx.items if it.layer == "pinned"]
            assert len(pinned_items) == 4
        finally:
            mem.close()
    finally:
        pass

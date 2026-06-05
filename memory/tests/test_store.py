"""``SqliteMemoryStore`` 单测：增删查、trigram 中文检索、supersede、pinned。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memory import EpisodicRow, SemanticRow, SqliteMemoryStore


def _now() -> datetime:
    return datetime.now(UTC)


def _semantic(statement: str, **kw: object) -> SemanticRow:
    now = _now()
    defaults: dict[str, object] = {
        "id": kw.pop("id", statement),  # 用 statement 当 id 方便断言
        "statement": statement,
        "persona_id": "p1",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kw)
    return SemanticRow(**defaults)  # type: ignore[arg-type]


def _store() -> SqliteMemoryStore:
    return SqliteMemoryStore(":memory:")


def test_add_and_search_semantic_chinese() -> None:
    store = _store()
    store.add_semantic(_semantic("用户养了一只叫Tom的猫"))
    store.add_semantic(_semantic("用户讨厌香菜"))

    # 共享 trigram "Tom" → 命中猫那条
    hits = store.search_semantic("Tom", owner_user_id="local", persona_id="p1", limit=10)
    assert [r.statement for r, _ in hits] == ["用户养了一只叫Tom的猫"]

    # 共享 trigram "讨厌香"/"厌香菜" → 命中香菜那条
    hits2 = store.search_semantic(
        "他很讨厌香菜的", owner_user_id="local", persona_id="p1", limit=10
    )
    assert any(r.statement == "用户讨厌香菜" for r, _ in hits2)


def test_search_too_short_query_returns_empty() -> None:
    store = _store()
    store.add_semantic(_semantic("用户养了一只猫"))
    # 单字凑不出 trigram
    assert store.search_semantic("猫", owner_user_id="local", persona_id="p1", limit=10) == []


def test_supersede_excludes_from_search_and_related() -> None:
    store = _store()
    store.add_semantic(_semantic("用户在腾讯工作", id="old"))
    assert store.supersede_semantic("old", _now()) is True

    hits = store.search_semantic("用户在腾讯工作", owner_user_id="local", persona_id="p1", limit=10)
    assert hits == []
    related = store.related_semantic(owner_user_id="local", persona_id="p1")
    assert [r.id for r in related] == []


def test_supersede_missing_returns_false() -> None:
    store = _store()
    assert store.supersede_semantic("nope", _now()) is False


def test_pinned_cross_persona_shared() -> None:
    store = _store()
    store.add_semantic(_semantic("用户叫小明", id="name", pinned=True, persona_id="p1"))
    store.add_semantic(_semantic("用户喜欢篮球", id="hobby", pinned=False, persona_id="p1"))

    # pinned 只按 owner 过滤，不按 persona —— 换一个 persona 也能取到
    pinned = store.pinned(owner_user_id="local", persona_id="p2")
    assert [r.id for r in pinned] == ["name"]


def test_episodic_persona_isolated() -> None:
    store = _store()
    now = _now()
    store.add_episodic(
        EpisodicRow(
            id="e1",
            summary="用户分享了新养的猫Tom",
            source_ref="s1#a..b",
            persona_id="p1",
            occurred_at=now,
            created_at=now,
        )
    )
    # 同 persona 命中
    hit = store.search_episodic("聊到Tom", owner_user_id="local", persona_id="p1", limit=10)
    assert [e.summary for e, _ in hit] == ["用户分享了新养的猫Tom"]
    # 异 persona 隔离
    assert store.search_episodic("聊到Tom", owner_user_id="local", persona_id="p2", limit=10) == []


def test_related_semantic_recent_first() -> None:
    store = _store()
    old = _semantic("旧事实", id="old", updated_at=_now() - timedelta(days=5))
    new = _semantic("新事实", id="new", updated_at=_now())
    store.add_semantic(old)
    store.add_semantic(new)
    related = store.related_semantic(owner_user_id="local", persona_id="p1")
    assert related[0].id == "new"

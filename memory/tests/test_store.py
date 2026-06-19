"""``SqliteMemoryStore`` 单测：增删查、中文检索（pass-1: jieba）、supersede、pinned、v1→v2 迁移。"""

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


def test_search_single_char_query_now_hits_via_jieba() -> None:
    """pass-1：jieba 把单字也切成 token，单字 query "猫" 能命中含 "猫" 词的记忆。

    pre-pass-1（trigram）行为：单字凑不出 trigram → 空。
    pass-1（jieba）行为：jieba.cut("猫") = ["猫"]，作为完整词 match。
    """
    store = _store()
    store.add_semantic(_semantic("用户养了一只猫"))
    hits = store.search_semantic("猫", owner_user_id="local", persona_id="p1", limit=10)
    assert [r.statement for r, _ in hits] == ["用户养了一只猫"]


def test_search_empty_or_punctuation_query_returns_empty() -> None:
    """空字符串 / 纯标点 query 仍返回空（_fts_query 过滤无字母数字的 token）。"""
    store = _store()
    store.add_semantic(_semantic("用户养了一只猫"))
    assert store.search_semantic("", owner_user_id="local", persona_id="p1", limit=10) == []
    assert store.search_semantic("。，！", owner_user_id="local", persona_id="p1", limit=10) == []


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


# ===== pass-1 M13.2：jieba 中文分词召回 + fts_match_pinned =====


def test_search_two_char_chinese_word_hits_via_jieba() -> None:
    """pass-1：2 字常见中文词（如"宠物"）通过 jieba 切词命中。

    pre-pass-1（trigram）：2 字凑不出 3-gram，空。
    pass-1（jieba）：jieba.cut("宠物") = ["宠物"]，命中含 "宠物" 词的记忆。
    """
    store = _store()
    store.add_semantic(_semantic("用户养了宠物Tom"))
    store.add_semantic(_semantic("用户讨厌香菜"))
    hits = store.search_semantic("宠物", owner_user_id="local", persona_id="p1", limit=10)
    assert [r.statement for r, _ in hits] == ["用户养了宠物Tom"]


def test_search_long_chinese_query_hits_via_shared_word() -> None:
    """pass-1：长 query 中包含 pinned/episodic 里的 jieba 词，能命中。

    pre-pass-1（trigram）：query "我叫什么名字" 的 trigram 与 "用户名字是张小红" 的
    trigram 无共享，召不到。
    pass-1（jieba）：两端共享 jieba 词 "名字"，命中。
    """
    store = _store()
    store.add_semantic(_semantic("用户名字是张小红"))
    store.add_semantic(_semantic("用户讨厌香菜"))
    hits = store.search_semantic("我叫什么名字", owner_user_id="local", persona_id="p1", limit=10)
    assert any(r.statement == "用户名字是张小红" for r, _ in hits)


def test_fts_match_pinned_returns_only_query_relevant_pinned() -> None:
    """pinned 是 user 维度；只有共享 jieba 词的 pinned 条目被返回。

    M13.2 的 fts_match_pinned 是"任一 jieba 词共享即命中"，不做"共享词数量阈值"——
    阈值算法是 M13.3 pinned gate（design §5.3 严格/宽松/动态档）的范围。所以这里
    选 query 时刻意避开会跟另一条 pinned 共享单字（如"叫"/"我"）的措辞。
    """
    store = _store()
    store.add_semantic(_semantic("用户名字是张小红", id="p-name", pinned=True))
    store.add_semantic(_semantic("用户的弟弟叫张小明", id="p-bro", pinned=True))
    store.add_semantic(_semantic("用户讨厌香菜", id="ord"))  # 非 pinned

    # query "什么名字" 切 [什么 / 名字]，只共享 "名字" → 命中 p-name
    hit_name = store.fts_match_pinned("什么名字", owner_user_id="local")
    assert hit_name == {"p-name"}

    # query "关于弟弟" 切 [关于 / 弟弟]，只共享 "弟弟" → 命中 p-bro
    hit_bro = store.fts_match_pinned("关于弟弟", owner_user_id="local")
    assert hit_bro == {"p-bro"}


def test_fts_match_pinned_returns_empty_when_unrelated() -> None:
    """query 与所有 pinned 无 jieba 共享词时返回空集——pinned gate 的核心收益。"""
    store = _store()
    store.add_semantic(_semantic("用户名字是张小红", id="p-name", pinned=True))
    hit = store.fts_match_pinned("周末去哪儿玩好", owner_user_id="local")
    assert hit == set()


def test_fts_match_pinned_ignores_non_pinned_even_if_matched() -> None:
    """非 pinned 即使共享 jieba 词也不返回。"""
    store = _store()
    store.add_semantic(_semantic("用户的名字是张小红", id="ord", pinned=False))
    hit = store.fts_match_pinned("我叫什么名字", owner_user_id="local")
    assert hit == set()


def test_fts_match_pinned_empty_query_returns_empty() -> None:
    """空 query / 纯标点 query 不应误返回任何 pinned。"""
    store = _store()
    store.add_semantic(_semantic("用户名字是张小红", id="p-name", pinned=True))
    assert store.fts_match_pinned("", owner_user_id="local") == set()
    assert store.fts_match_pinned("。，！", owner_user_id="local") == set()


def test_list_semantic_order_and_owner_filter() -> None:
    """026: list_semantic 按 created_at DESC 返回，且按 owner 过滤。"""
    store = _store()
    old = _semantic("旧事实", id="old", created_at=_now() - timedelta(days=2))
    new = _semantic("新事实", id="new", created_at=_now() - timedelta(days=1))
    other_owner = _semantic("他人事实", id="other", owner_user_id="other")
    store.add_semantic(old)
    store.add_semantic(new)
    store.add_semantic(other_owner)

    rows = store.list_semantic(owner_user_id="local", limit=10)
    assert [r.id for r in rows] == ["new", "old"]


def test_list_semantic_pagination() -> None:
    """026: list_semantic LIMIT/OFFSET 分页行为。"""
    store = _store()
    for i in range(3):
        store.add_semantic(
            _semantic(f"事实{i}", id=f"s{i}", created_at=_now() - timedelta(hours=i))
        )

    first = store.list_semantic(owner_user_id="local", limit=2, offset=0)
    assert [r.id for r in first] == ["s0", "s1"]
    second = store.list_semantic(owner_user_id="local", limit=2, offset=2)
    assert [r.id for r in second] == ["s2"]


def test_list_episodic_persona_filter() -> None:
    """026: list_episodic 按 persona 过滤；persona_id=None 时返回全部。"""
    store = _store()
    now = _now()
    store.add_episodic(
        EpisodicRow(
            id="e1",
            summary="p1 的事",
            source_ref="s1#a..b",
            persona_id="p1",
            occurred_at=now,
            created_at=now,
        )
    )
    store.add_episodic(
        EpisodicRow(
            id="e2",
            summary="p2 的事",
            source_ref="s1#c..d",
            persona_id="p2",
            occurred_at=now - timedelta(hours=1),
            created_at=now - timedelta(hours=1),
        )
    )

    p1_rows = store.list_episodic(owner_user_id="local", persona_id="p1")
    assert [r.id for r in p1_rows] == ["e1"]

    all_rows = store.list_episodic(owner_user_id="local", persona_id=None)
    assert [r.id for r in all_rows] == ["e1", "e2"]


def test_list_episodic_excludes_deleted() -> None:
    """026: list_episodic 排除 deleted_at 非空的行。"""
    store = _store()
    now = _now()
    store.add_episodic(
        EpisodicRow(
            id="e1",
            summary="活跃",
            source_ref="s1#a..b",
            persona_id="p1",
            occurred_at=now,
            created_at=now,
        )
    )
    store.add_episodic(
        EpisodicRow(
            id="e2",
            summary="已删",
            source_ref="s1#c..d",
            persona_id="p1",
            occurred_at=now,
            created_at=now,
            deleted_at=now,
        )
    )
    rows = store.list_episodic(owner_user_id="local", persona_id="p1")
    assert [r.id for r in rows] == ["e1"]


def test_v1_to_v2_lazy_migration() -> None:
    """旧库（v1 trigram FTS5 / 无 *_tokens 列）启动时自动升 v2 并回填。"""
    import sqlite3
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as tmp:
        db = _Path(tmp) / "v1.db"

        # 手搓 v1 库：旧 schema，trigram FTS5，无 *_tokens 列，schema_version=1
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1');
            CREATE TABLE semantic (
                id TEXT PRIMARY KEY, statement TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5, pinned INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'extracted', speaker_origin TEXT NOT NULL DEFAULT 'user',
                valid_from TEXT, valid_until TEXT, provenance TEXT NOT NULL DEFAULT '[]',
                deleted_at TEXT, owner_user_id TEXT NOT NULL DEFAULT 'local',
                persona_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE episodic (
                id TEXT PRIMARY KEY, summary TEXT NOT NULL, source_ref TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5, participants TEXT NOT NULL DEFAULT '[]',
                occurred_at TEXT NOT NULL, deleted_at TEXT,
                owner_user_id TEXT NOT NULL DEFAULT 'local', persona_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE semantic_fts
                USING fts5(statement, content='semantic', content_rowid='rowid', tokenize='trigram');
            CREATE VIRTUAL TABLE episodic_fts
                USING fts5(summary, content='episodic', content_rowid='rowid', tokenize='trigram');
            INSERT INTO semantic(id, statement, persona_id, created_at, updated_at)
                VALUES ('s1', '用户养了宠物Tom', 'p1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO semantic_fts(rowid, statement)
                SELECT rowid, statement FROM semantic;
            """
        )
        conn.commit()
        conn.close()

        # 用 SqliteMemoryStore 打开旧库 → 应自动迁移
        store = SqliteMemoryStore(str(db))
        try:
            # 验证 schema_version 升到 2
            meta = store._conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            assert meta["value"] == "2"

            # 验证新列加好
            cols = {
                r["name"] for r in store._conn.execute("PRAGMA table_info(semantic)").fetchall()
            }
            assert "statement_tokens" in cols
            cols = {
                r["name"] for r in store._conn.execute("PRAGMA table_info(episodic)").fetchall()
            }
            assert "summary_tokens" in cols

            # 验证旧数据可被 jieba 召回（"宠物" 通过新 FTS5 命中）
            hits = store.search_semantic("宠物", owner_user_id="local", persona_id="p1", limit=10)
            assert [r.statement for r, _ in hits] == ["用户养了宠物Tom"]
        finally:
            store.close()

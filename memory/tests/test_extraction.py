"""抽取写路径单测：Extractor 解析、Reconciler 落库、AsyncExtractionWorker。

不触发任何真实 LLM —— 用 fake client / fake store。
"""

from __future__ import annotations

from datetime import UTC, datetime

from memory.extraction.result import ExtractionOutput, ExtractionResult, SemanticOp

from memory import (
    ConversationFragment,
    ExtractionAttempt,
    Extractor,
    Reconciler,
    SemanticRow,
    Utterance,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _fragment(*texts: tuple[str, str]) -> ConversationFragment:
    utts = [
        Utterance(speaker=spk, text=txt, ts=_now(), source_ref=f"s1#{i}")  # type: ignore[arg-type]
        for i, (spk, txt) in enumerate(texts)
    ]
    return ConversationFragment(session_id="s1", utterances=utts, persona_id="p1")


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict[str, object]]] = []

    def complete(self, messages: list[dict[str, object]], **_kw: object) -> str:
        self.calls.append(messages)
        return self.reply


def _extractor(reply: str) -> Extractor:
    return Extractor(FakeLLM(reply), prompt="x")  # type: ignore[arg-type]


# ----- Extractor 解析 -----


def test_extractor_parses_add_and_supersede() -> None:
    reply = (
        '{"episodic_entries": ["用户分享养猫"],'
        ' "semantic_ops": ['
        '{"op": "add", "statement": "用户养了猫Tom", "importance": 0.7, "pinned": false, "speaker_origin": "user"},'
        '{"op": "supersede", "target_hint": "用户在腾讯工作", "statement": "用户在字节工作"}'
        "]}"
    )
    out = _extractor(reply).extract(_fragment(("user", "我养了猫")), [])
    assert out.episodic_entries == ["用户分享养猫"]
    assert len(out.semantic_ops) == 2
    assert out.semantic_ops[0].op == "add"
    assert out.semantic_ops[1].op == "supersede"
    assert out.semantic_ops[1].target_hint == "用户在腾讯工作"


def test_extractor_whitelists_core_and_fact_keys() -> None:
    reply = (
        '{"episodic_entries": [], "semantic_ops": ['
        '{"op":"add","statement":"用户名字是小明","memory_class":"core",'
        '"fact_key":"user.name"},'
        '{"op":"add","statement":"用户今天喝了咖啡","memory_class":"core",'
        '"fact_key":"user.random"}'
        "]}"
    )
    out = _extractor(reply).extract(_fragment(("user", "我叫小明，今天喝了咖啡")), [])

    assert out.semantic_ops[0].memory_class == "core"
    assert out.semantic_ops[0].fact_key == "user.name"
    assert out.semantic_ops[1].memory_class == "priority"
    assert out.semantic_ops[1].fact_key is None


def test_extractor_derives_storage_metadata_from_pass1_contract() -> None:
    reply = (
        '{"episodic_entries": [], "semantic_ops": ['
        '{"op":"add","statement":"用户名字是小明","pinned":true},'
        '{"op":"add","statement":"用户在字节工作","pinned":false},'
        '{"op":"add","statement":"用户居住在杭州","pinned":false},'
        '{"op":"add","statement":"用户喜欢缅因猫","pinned":false}'
        "]}"
    )
    out = _extractor(reply).extract(_fragment(("user", "我叫小明，在字节工作，住在杭州")), [])

    assert [op.fact_key for op in out.semantic_ops] == [
        "user.name",
        "user.employer",
        "user.residence",
        None,
    ]
    assert [op.memory_class for op in out.semantic_ops] == [
        "core",
        "priority",
        "priority",
        "normal",
    ]
    assert [op.pinned for op in out.semantic_ops] == [True, True, True, False]


def test_extractor_rejects_allowed_but_semantically_wrong_fact_key() -> None:
    reply = (
        '{"episodic_entries": [], "semantic_ops": ['
        '{"op":"add","statement":"用户喜欢咖啡","memory_class":"core",'
        '"fact_key":"user.name"}'
        "]}"
    )
    out = _extractor(reply).extract(_fragment(("user", "我喜欢咖啡")), [])

    assert out.semantic_ops[0].fact_key is None
    assert out.semantic_ops[0].memory_class == "priority"


def test_extractor_and_reconciler_can_replay_pass1_metadata_without_upgrade() -> None:
    reply = (
        '{"episodic_entries": [], "semantic_ops": ['
        '{"op":"add","statement":"用户在字节工作","pinned":false}'
        "]}"
    )
    output = Extractor(
        FakeLLM(reply),  # type: ignore[arg-type]
        prompt="x",
        normalize_metadata=False,
    ).extract(_fragment(("user", "我在字节工作")), [])
    store = FakeStore()

    Reconciler(store, normalize_metadata=False).apply(
        output, _fragment(("user", "我在字节工作")), []
    )

    assert output.semantic_ops[0].fact_key is None
    assert output.semantic_ops[0].memory_class == "normal"
    assert output.semantic_ops[0].pinned is False
    assert store.semantics[0].fact_key is None
    assert store.semantics[0].memory_class == "normal"
    assert store.semantics[0].pinned is False


def test_extractor_legacy_episodic_summary_falls_back_to_single_entry() -> None:
    """旧 prompt 输出 ``episodic_summary: str`` 仍应被解析（兼容性）。"""
    reply = '{"episodic_summary": "用户分享养猫", "semantic_ops": []}'
    out = _extractor(reply).extract(_fragment(("user", "x")), [])
    assert out.episodic_entries == ["用户分享养猫"]


def test_extractor_legacy_episodic_summary_null_yields_empty_entries() -> None:
    reply = '{"episodic_summary": null, "semantic_ops": []}'
    out = _extractor(reply).extract(_fragment(("user", "x")), [])
    assert out.episodic_entries == []
    assert out.is_noop()


def test_extractor_strips_code_fence() -> None:
    reply = '```json\n{"episodic_entries": [], "semantic_ops": []}\n```'
    out = _extractor(reply).extract(_fragment(("user", "hi")), [])
    assert out.is_noop()


def test_extractor_bad_json_returns_noop() -> None:
    out = _extractor("not json at all").extract(_fragment(("user", "hi")), [])
    assert out.is_noop()


def test_extractor_llm_error_returns_noop() -> None:
    class BoomLLM:
        def complete(self, messages: list[dict[str, object]], **_kw: object) -> str:
            raise RuntimeError("boom")

    out = Extractor(BoomLLM(), prompt="x").extract(_fragment(("user", "hi")), [])  # type: ignore[arg-type]
    assert out.is_noop()


def test_extractor_reports_success_empty_invalid_and_error_attempts() -> None:
    attempts: list[ExtractionAttempt] = []
    fragment = _fragment(("user", "我养猫"))

    success = Extractor(
        FakeLLM('{"episodic_entries":["用户养猫"],"semantic_ops":[]}'),  # type: ignore[arg-type]
        prompt="x",
        on_attempt=attempts.append,
    )
    success.extract(fragment, [])
    Extractor(FakeLLM("{}"), prompt="x", on_attempt=attempts.append).extract(  # type: ignore[arg-type]
        fragment, []
    )
    Extractor(FakeLLM("not json"), prompt="x", on_attempt=attempts.append).extract(  # type: ignore[arg-type]
        fragment, []
    )

    class BoomLLM:
        def complete(self, messages: list[dict[str, object]], **_kw: object) -> str:
            raise RuntimeError("boom")

    Extractor(BoomLLM(), prompt="x", on_attempt=attempts.append).extract(fragment, [])  # type: ignore[arg-type]

    assert [attempt.status for attempt in attempts] == [
        "success",
        "empty_output",
        "invalid_json",
        "llm_error",
    ]
    assert attempts[0].episodic_count == 1
    assert attempts[0].raw_response is not None
    assert attempts[-1].error_type == "RuntimeError"
    assert attempts[-1].raw_response is None


# ----- Reconciler 落库 -----


class FakeStore:
    def __init__(self) -> None:
        self.semantics: list[SemanticRow] = []
        self.episodics: list[object] = []
        self.superseded: list[str] = []

    def add_semantic(self, row: SemanticRow) -> None:
        self.semantics.append(row)

    def add_episodic(self, row: object) -> None:
        self.episodics.append(row)

    def supersede_semantic(self, old_id: str, _ts: datetime) -> bool:
        self.superseded.append(old_id)
        return True

    def related_semantic(self, **_kw: object) -> list[SemanticRow]:
        return []


def test_reconciler_add_applies_user_said_bonus() -> None:
    store = FakeStore()
    out = ExtractionOutput(
        episodic_entries=["摘要"],
        semantic_ops=[
            SemanticOp(op="add", statement="用户喜欢猫", importance=0.5, speaker_origin="user")
        ],
    )
    result = Reconciler(store).apply(out, _fragment(("user", "我喜欢猫")), [])

    assert len(store.semantics) == 1
    assert abs(store.semantics[0].importance - 0.65) < 1e-9  # 0.5 + 0.15 user 加成
    assert len(store.episodics) == 1
    assert result.added_semantic == ["用户喜欢猫"]
    assert len(result.episodic_ids) == 1


def test_reconciler_writes_one_episodic_per_entry() -> None:
    """多条 episodic_entries 应各自写一行 EpisodicRow，episodic_ids 累积全部。"""
    store = FakeStore()
    out = ExtractionOutput(
        episodic_entries=["条目一", "条目二", "条目三"],
        semantic_ops=[],
    )
    result = Reconciler(store).apply(out, _fragment(("user", "x")), [])

    assert len(store.episodics) == 3
    assert len(result.episodic_ids) == 3
    # 每条 episodic 的 id 都不同
    assert len({ep.id for ep in store.episodics}) == 3  # type: ignore[attr-defined]


def test_reconciler_semantic_provenance_links_all_episodics() -> None:
    """多条 episodic 时，semantic.provenance 应包含全部 episodic_id。"""
    store = FakeStore()
    out = ExtractionOutput(
        episodic_entries=["e1", "e2"],
        semantic_ops=[SemanticOp(op="add", statement="某事实", speaker_origin="user")],
    )
    result = Reconciler(store).apply(out, _fragment(("user", "x")), [])
    assert len(store.semantics) == 1
    assert sorted(store.semantics[0].provenance) == sorted(result.episodic_ids)


def test_reconciler_agent_said_no_bonus() -> None:
    store = FakeStore()
    out = ExtractionOutput(
        episodic_entries=[],
        semantic_ops=[SemanticOp(op="add", statement="x", importance=0.5, speaker_origin="agent")],
    )
    Reconciler(store).apply(out, _fragment(("agent", "...")), [])
    assert abs(store.semantics[0].importance - 0.5) < 1e-9


def test_reconciler_supersede_matches_existing() -> None:
    store = FakeStore()
    old = SemanticRow(
        id="old", statement="用户在腾讯工作", persona_id="p1", created_at=_now(), updated_at=_now()
    )
    out = ExtractionOutput(
        episodic_entries=[],
        semantic_ops=[
            SemanticOp(op="supersede", statement="用户在字节工作", target_hint="用户在腾讯工作")
        ],
    )
    result = Reconciler(store).apply(out, _fragment(("user", "我换工作了")), [old])

    assert store.superseded == ["old"]
    assert result.superseded_semantic == ["用户在腾讯工作"]
    assert "用户在字节工作" in result.added_semantic


def test_reconciler_supersede_no_match_degrades_to_add() -> None:
    store = FakeStore()
    out = ExtractionOutput(
        episodic_entries=[],
        semantic_ops=[SemanticOp(op="supersede", statement="新事实", target_hint="不存在的旧事实")],
    )
    result = Reconciler(store).apply(out, _fragment(("user", "x")), [])
    assert store.superseded == []
    assert result.added_semantic == ["新事实"]


def test_reconciler_prefers_fact_key_and_builds_version_chain() -> None:
    class FactStore(FakeStore):
        def __init__(self, current: SemanticRow) -> None:
            super().__init__()
            self.current = current

        def semantic_by_fact_key(self, fact_key: str, **_kw: object) -> SemanticRow | None:
            return self.current if fact_key == "user.employer" else None

        def supersede_and_add_semantic(self, old_id: str, row: SemanticRow, _ts: datetime) -> bool:
            self.superseded.append(old_id)
            self.semantics.append(row)
            return True

    old = SemanticRow(
        id="old",
        statement="用户在腾讯工作",
        persona_id="p1",
        created_at=_now(),
        updated_at=_now(),
        fact_key="user.employer",
    )
    store = FactStore(old)
    out = ExtractionOutput(
        semantic_ops=[
            SemanticOp(
                op="add",
                statement="用户在字节工作",
                memory_class="priority",
                fact_key="user.employer",
            )
        ]
    )

    result = Reconciler(store).apply(out, _fragment(("user", "我换到字节了")), [])

    assert store.superseded == ["old"]
    assert store.semantics[0].supersedes_id == "old"
    assert result.superseded_semantic == ["用户在腾讯工作"]


# ----- Worker -----


def test_worker_processes_and_callbacks() -> None:
    from memory.extraction import AsyncExtractionWorker

    store = FakeStore()
    reply = '{"episodic_entries": [], "semantic_ops": [{"op": "add", "statement": "用户养猫"}]}'
    extractor = _extractor(reply)
    results: list[ExtractionResult] = []
    worker = AsyncExtractionWorker(store, extractor, Reconciler(store), on_extracted=results.append)  # type: ignore[arg-type]

    worker.submit(_fragment(("user", "我养猫")))
    worker.flush()

    assert [r.added_semantic for r in results] == [["用户养猫"]]
    assert len(store.semantics) == 1
    worker.close()


def test_worker_submit_after_close_ignored() -> None:
    from memory.extraction import AsyncExtractionWorker

    store = FakeStore()
    worker = AsyncExtractionWorker(store, _extractor("{}"), Reconciler(store))  # type: ignore[arg-type]
    worker.close()
    worker.submit(_fragment(("user", "x")))  # 不应抛
    assert store.semantics == []


def test_worker_error_isolation() -> None:
    from memory.extraction import AsyncExtractionWorker

    class BoomExtractor(Extractor):
        def __init__(self) -> None:  # 不调父构造
            pass

        def extract(self, *_a: object, **_k: object) -> ExtractionOutput:
            raise RuntimeError("boom")

    store = FakeStore()
    worker = AsyncExtractionWorker(store, BoomExtractor(), Reconciler(store))  # type: ignore[arg-type]
    worker.submit(_fragment(("user", "x")))
    worker.flush()  # 不应卡死 / 不应抛
    worker.close()

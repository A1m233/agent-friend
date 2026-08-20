from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memory.extraction import (
    AsyncExtractionWorker,
    CoreUpdate,
    ExtractionContextBuilder,
    ExtractionOutput,
    ExtractionResult,
    Extractor,
    Reconciler,
    SemanticChange,
    SemanticFactDraft,
    SemanticOp,
    SemanticRetirement,
)
from memory.extraction.extractor import _PROMPT_PATH, _parse_output
from memory.extraction.reconciler import ExtractionApplyError
from memory.store import SemanticRow

from llm_providers import ModelRequest, ModelResponse
from memory import ConversationFragment, SqliteMemoryStore, Utterance

_REASON = "测试中的分类理由"


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.messages: list[dict[str, object]] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.messages = request.messages
        return ModelResponse(text=self.reply)


def _fragment(text: str = "以后叫我小陈，我目前住在深圳") -> ConversationFragment:
    return ConversationFragment(
        session_id="s1",
        persona_id="p1",
        utterances=[
            Utterance(
                speaker="user",
                text=text,
                ts=datetime(2026, 8, 15, tzinfo=UTC),
                source_ref="s1#event-1",
            )
        ],
    )


def _semantic(row_id: str, statement: str) -> SemanticRow:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SemanticRow(
        id=row_id,
        statement=statement,
        persona_id="p1",
        created_at=now,
        updated_at=now,
        valid_from=now,
    )


def test_core_relationship_prompt_uses_semantic_boundary_examples() -> None:
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    assert "以下示例只说明语义边界，不是要匹配其中的字词或句式" in prompt
    assert "稳定关系定位" in prompt
    assert "情感态度不是关系字段" in prompt
    assert "其他人的关系仍是普通事实" in prompt
    assert "知心朋友" not in prompt


def test_extractor_parses_core_first_and_exact_semantic_reference() -> None:
    output = _parse_output(
        '{"core_updates":[{"op":"add","field":"user.preferred_name","value":"小陈",'
        '"reason":"用户明确指定称呼，属于核心称呼字段"}],'
        '"semantic_ops":[{"op":"supersede","target_ref":"M001",'
        '"statement":"用户目前在字节跳动工作","reason":"雇主不属于Core字段"}],'
        '"episodic_entries":["用户希望被称为小陈"]}'
    )
    assert output.core_updates == [
        CoreUpdate(
            op="add",
            field="user.preferred_name",
            value="小陈",
            reason="用户明确指定称呼，属于核心称呼字段",
        )
    ]
    assert output.semantic_ops[0].target_ref == "M001"
    assert output.episodic_entries == ["用户希望被称为小陈"]


def test_current_extraction_schema_rejects_operations_without_reasons() -> None:
    output = _parse_output(
        '{"core_updates":[{"op":"add","field":"relationship.kind",'
        '"value":"创作伙伴"}],"semantic_ops":[],"episodic_entries":[]}'
    )
    assert output.validation_error == "invalid_operation_shape_or_reason"
    store = SqliteMemoryStore(":memory:")
    fragment = _fragment("我们以后作为创作伙伴相处")
    with pytest.raises(ExtractionApplyError, match="invalid_operation_shape_or_reason"):
        Reconciler(store).apply(
            output,
            fragment,
            ExtractionContextBuilder(store).build(fragment),
        )


def test_extractor_renders_full_core_and_reference_addressable_semantic() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_semantic(_semantic("old-job", "用户目前在腾讯工作"))
    context = ExtractionContextBuilder(store).build(_fragment())
    llm = FakeLLM('{"core_updates":[],"semantic_ops":[],"episodic_entries":[]}')
    Extractor(llm, prompt="prompt").extract(_fragment(), context)  # type: ignore[arg-type]
    rendered = str(llm.messages[1]["content"])
    assert "[M001] 用户目前在腾讯工作" in rendered
    assert "当前完整 Core Profile" in rendered


def test_reconciler_atomically_adds_core_ordinary_semantic_and_episode() -> None:
    store = SqliteMemoryStore(":memory:")
    fragment = _fragment()
    context = ExtractionContextBuilder(store).build(fragment)
    output = ExtractionOutput(
        core_updates=[
            CoreUpdate(op="add", field="user.preferred_name", value="小陈", reason=_REASON),
            CoreUpdate(op="add", field="user.current_residences", value="深圳", reason=_REASON),
        ],
        semantic_ops=[SemanticOp(op="add", statement="用户喜欢喝手冲咖啡", reason=_REASON)],
        episodic_entries=["用户说以后希望被称为小陈"],
    )
    result = Reconciler(store).apply(output, fragment, context)
    assert result.committed is True
    assert store.core_profile(owner_user_id="local", persona_id="p1").as_dict() == {
        "user": {"preferred_name": "小陈", "current_residences": ["深圳"]},
        "relationship": {"kind": None, "agent_nickname": None},
    }
    semantic = store.list_semantic(owner_user_id="local")
    assert [row.statement for row in semantic] == ["用户喜欢喝手冲咖啡"]
    assert semantic[0].pinned is False
    assert semantic[0].memory_class == "normal"
    assert semantic[0].fact_key is None


def test_reconciler_requires_exact_visible_ref_and_rolls_back_whole_batch() -> None:
    store = SqliteMemoryStore(":memory:")
    fragment = _fragment()
    context = ExtractionContextBuilder(store).build(fragment)
    output = ExtractionOutput(
        core_updates=[
            CoreUpdate(op="add", field="user.preferred_name", value="小陈", reason=_REASON)
        ],
        semantic_ops=[
            SemanticOp(
                op="supersede",
                target_ref="M999",
                statement="用户目前在字节跳动工作",
                reason=_REASON,
            )
        ],
    )
    with pytest.raises(ExtractionApplyError, match="M999"):
        Reconciler(store).apply(output, fragment, context)
    assert store.core_profile(owner_user_id="local", persona_id="p1").is_empty()
    assert store.list_semantic(owner_user_id="local") == []


def test_legacy_supersede_converts_to_change_without_writing_scalar_lineage() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_semantic(_semantic("old-job", "用户目前在腾讯工作"))
    fragment = _fragment("我现在在字节跳动工作")
    context = ExtractionContextBuilder(store).build(fragment)
    output = ExtractionOutput(
        semantic_ops=[
            SemanticOp(
                op="supersede",
                target_ref="M001",
                statement="用户目前在字节跳动工作",
                reason=_REASON,
            )
        ]
    )
    Reconciler(store).apply(output, fragment, context)
    active = store.list_semantic(owner_user_id="local")
    assert [row.statement for row in active] == ["用户目前在字节跳动工作"]
    assert active[0].supersedes_id is None
    changes = store.list_semantic_changes(owner_user_id="local")
    assert len(changes) == 1
    assert {(member.role, member.semantic_id) for member in changes[0].members} == {
        ("retired", "old-job"),
        ("introduced", active[0].id),
    }


def test_extractor_parses_v2_many_to_many_change_and_contract_version() -> None:
    output = _parse_output(
        '{"core_updates":[],"semantic_changes":[{"retirements":['
        '{"target_ref":"M001","reason":"旧事实一不再准确"},'
        '{"target_ref":"M002","reason":"旧事实二不再准确"}],"new_facts":['
        '{"statement":"新事实一","reason":"普通长期事实","importance":0.8},'
        '{"statement":"新事实二","reason":"另一普通长期事实",'
        '"speaker_origin":"agent"}]}],"episodic_entries":[]}'
    )
    assert output.output_contract_version == 2
    assert output.validation_error is None
    assert output.semantic_changes == [
        SemanticChange(
            retirements=[
                SemanticRetirement("M001", "旧事实一不再准确"),
                SemanticRetirement("M002", "旧事实二不再准确"),
            ],
            new_facts=[
                SemanticFactDraft("新事实一", "普通长期事实", importance=0.8),
                SemanticFactDraft("新事实二", "另一普通长期事实", speaker_origin="agent"),
            ],
        )
    ]


def test_extractor_rejects_ambiguous_or_reasonless_v2_contract() -> None:
    ambiguous = _parse_output(
        '{"core_updates":[],"semantic_changes":[],"semantic_ops":[],"episodic_entries":[]}'
    )
    assert ambiguous.validation_error == "ambiguous_semantic_contract"
    reasonless = _parse_output(
        '{"core_updates":[],"semantic_changes":[{"retirements":['
        '{"target_ref":"M001","reason":""}],"new_facts":[]}],'
        '"episodic_entries":[]}'
    )
    assert reasonless.validation_error == "invalid_operation_shape_or_reason"


def test_reconciler_applies_many_to_many_change_and_preserves_member_reasons() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_semantic(_semantic("old-1", "用户每周运动三次"))
    store.add_semantic(_semantic("old-2", "用户通常隔天运动"))
    fragment = _fragment("前两种说法都不准，我周六骑行、周日徒步")
    context = ExtractionContextBuilder(store).build(fragment)
    refs = {row.id: ref for ref, row in context.semantic_facts}
    output = ExtractionOutput(
        semantic_changes=[
            SemanticChange(
                retirements=[
                    SemanticRetirement(refs["old-1"], "频率已被用户纠正"),
                    SemanticRetirement(refs["old-2"], "间隔说法已被用户纠正"),
                ],
                new_facts=[
                    SemanticFactDraft("用户通常周六骑行", "稳定活动习惯"),
                    SemanticFactDraft("用户通常周日徒步", "另一项稳定活动习惯"),
                ],
            )
        ]
    )
    result = Reconciler(store).apply(output, fragment, context)
    assert len(result.semantic_change_ids) == 1
    assert set(result.retired_semantic) == {
        "用户每周运动三次",
        "用户通常隔天运动",
    }
    assert {row.statement for row in store.list_semantic(owner_user_id="local")} == {
        "用户通常周六骑行",
        "用户通常周日徒步",
    }
    change = store.list_semantic_changes(owner_user_id="local")[0]
    assert change.id == result.semantic_change_ids[0]
    assert {member.reason for member in change.members} == {
        "频率已被用户纠正",
        "间隔说法已被用户纠正",
        "稳定活动习惯",
        "另一项稳定活动习惯",
    }


def test_reconciler_retracts_without_new_fact_or_soft_delete() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_semantic(_semantic("old-plan", "用户正在准备资格考试"))
    fragment = _fragment("我已经取消考试计划")
    context = ExtractionContextBuilder(store).build(fragment)
    ref = context.semantic_facts[0][0]
    Reconciler(store).apply(
        ExtractionOutput(
            semantic_changes=[
                SemanticChange(retirements=[SemanticRetirement(ref, "用户明确取消了计划")])
            ]
        ),
        fragment,
        context,
    )
    assert store.list_semantic(owner_user_id="local") == []
    historical = store._conn.execute(
        "SELECT valid_until, deleted_at FROM semantic WHERE id = 'old-plan'"
    ).fetchone()
    assert historical["valid_until"] is not None
    assert historical["deleted_at"] is None


def test_reconciler_rolls_back_entire_change_when_one_target_is_stale() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_semantic(_semantic("old-a", "旧事实 A"))
    store.add_semantic(_semantic("old-b", "旧事实 B"))
    fragment = _fragment("纠正两条旧事实")
    context = ExtractionContextBuilder(store).build(fragment)
    refs = {row.id: ref for ref, row in context.semantic_facts}
    store.supersede_semantic("old-b", datetime(2026, 8, 16, tzinfo=UTC))
    with pytest.raises(ExtractionApplyError) as error:
        Reconciler(store).apply(
            ExtractionOutput(
                semantic_changes=[
                    SemanticChange(
                        retirements=[
                            SemanticRetirement(refs["old-a"], "需要纠正"),
                            SemanticRetirement(refs["old-b"], "需要纠正"),
                        ],
                        new_facts=[SemanticFactDraft("新事实", "稳定普通事实")],
                    )
                ]
            ),
            fragment,
            context,
        )
    assert error.value.code == "stale_semantic_ref"
    active = store.list_semantic(owner_user_id="local")
    assert [row.id for row in active] == ["old-a"]
    assert store.list_semantic_changes(owner_user_id="local") == []


def test_reconciler_observation_identity_is_idempotent_even_for_empty_output() -> None:
    store = SqliteMemoryStore(":memory:")
    fragment = _fragment()
    context = ExtractionContextBuilder(store).build(fragment)
    first = Reconciler(store).apply(ExtractionOutput(), fragment, context)
    second = Reconciler(store).apply(ExtractionOutput(), fragment, context)
    assert first.committed is True
    assert second.committed is False
    assert second.duplicate is True


def test_single_core_field_requires_replace_and_matching_c_ref() -> None:
    store = SqliteMemoryStore(":memory:")
    fragment = _fragment()
    first_context = ExtractionContextBuilder(store).build(fragment)
    Reconciler(store).apply(
        ExtractionOutput(
            core_updates=[
                CoreUpdate(op="add", field="user.preferred_name", value="小陈", reason=_REASON)
            ]
        ),
        fragment,
        first_context,
    )
    second_fragment = ConversationFragment(
        session_id="s1",
        persona_id="p1",
        utterances=[
            Utterance(
                speaker="user",
                text="改叫我陈老师",
                ts=datetime(2026, 8, 15, 0, 1, tzinfo=UTC),
                source_ref="s1#event-2",
            )
        ],
    )
    context = ExtractionContextBuilder(store).build(second_fragment)
    core_ref = next(
        ref for ref, row in context.core_entries if row.field_key == "user.preferred_name"
    )
    Reconciler(store).apply(
        ExtractionOutput(
            core_updates=[
                CoreUpdate(
                    op="replace",
                    field="user.preferred_name",
                    target_ref=core_ref,
                    value="陈老师",
                    reason=_REASON,
                )
            ]
        ),
        second_fragment,
        context,
    )
    assert store.core_profile(owner_user_id="local", persona_id="p1").as_dict()["user"] == {
        "preferred_name": "陈老师",
        "current_residences": [],
    }


def test_worker_reports_rejected_batch_without_writing() -> None:
    store = SqliteMemoryStore(":memory:")
    llm = FakeLLM(
        '{"core_updates":[],"semantic_ops":[{"op":"supersede",'
        '"target_ref":"M404","statement":"无目标的新事实",'
        '"reason":"普通长期事实"}],"episodic_entries":[]}'
    )
    results: list[ExtractionResult] = []
    worker = AsyncExtractionWorker(
        store,
        Extractor(llm, prompt="prompt"),  # type: ignore[arg-type]
        Reconciler(store),
        on_extracted=results.append,
    )
    worker.submit(_fragment())
    worker.flush()
    worker.close()

    assert results[0].committed is False
    assert results[0].error_code == "unknown_semantic_ref"
    assert store.list_semantic(owner_user_id="local") == []


def test_worker_rejects_missing_reason_without_committing_episode() -> None:
    store = SqliteMemoryStore(":memory:")
    llm = FakeLLM(
        '{"core_updates":[{"op":"add","field":"relationship.kind",'
        '"value":"创作伙伴"}],"semantic_ops":[],"episodic_entries":["用户定义了关系"]}'
    )
    results: list[ExtractionResult] = []
    worker = AsyncExtractionWorker(
        store,
        Extractor(llm, prompt="prompt"),  # type: ignore[arg-type]
        Reconciler(store),
        on_extracted=results.append,
    )
    worker.submit(_fragment("我们以后作为创作伙伴相处"))
    worker.flush()
    worker.close()

    assert results[0].committed is False
    assert results[0].error_code == "invalid_extraction_output"
    assert store.core_profile(owner_user_id="local", persona_id="p1").is_empty()
    assert store.list_episodic(owner_user_id="local") == []


def test_current_residences_support_multiple_values_removal_and_history() -> None:
    store = SqliteMemoryStore(":memory:")
    first = _fragment("我目前在深圳和香港两边住")
    Reconciler(store).apply(
        ExtractionOutput(
            core_updates=[
                CoreUpdate(op="add", field="user.current_residences", value="深圳", reason=_REASON),
                CoreUpdate(op="add", field="user.current_residences", value="香港", reason=_REASON),
            ]
        ),
        first,
        ExtractionContextBuilder(store).build(first),
    )
    second = ConversationFragment(
        session_id="s1",
        persona_id="p1",
        utterances=[
            Utterance(
                speaker="user",
                text="我已经不住深圳了",
                ts=datetime(2026, 8, 16, tzinfo=UTC),
                source_ref="s1#event-2",
            )
        ],
    )
    context = ExtractionContextBuilder(store).build(second)
    shenzhen_ref = next(ref for ref, row in context.core_entries if row.value == "深圳")
    Reconciler(store).apply(
        ExtractionOutput(
            core_updates=[
                CoreUpdate(
                    op="remove",
                    field="user.current_residences",
                    target_ref=shenzhen_ref,
                    reason=_REASON,
                )
            ]
        ),
        second,
        context,
    )

    profile = store.core_profile(owner_user_id="local", persona_id="p1").as_dict()
    assert profile["user"] == {"preferred_name": None, "current_residences": ["香港"]}
    history = store.list_core_profile_entries(
        owner_user_id="local", persona_id="p1", include_history=True
    )
    old_shenzhen = next(row for row in history if row.value == "深圳")
    assert old_shenzhen.valid_until is not None
    assert old_shenzhen.provenance


def test_core_profile_can_be_invalidated_by_direct_fragment_source() -> None:
    store = SqliteMemoryStore(":memory:")
    fragment = _fragment("以后叫我小陈")
    Reconciler(store).apply(
        ExtractionOutput(
            core_updates=[
                CoreUpdate(op="add", field="user.preferred_name", value="小陈", reason=_REASON)
            ]
        ),
        fragment,
        ExtractionContextBuilder(store).build(fragment),
    )

    active = store.list_core_profile_entries(owner_user_id="local", persona_id="p1")
    assert active[0].provenance == ("s1#event-1",)

    counts = store.soft_delete_by_source_events(
        session_id="s1",
        event_uuids={"event-1"},
        deleted_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    assert counts == {"episodic": 0, "semantic": 0, "core": 1}
    assert store.core_profile(owner_user_id="local", persona_id="p1").is_empty()

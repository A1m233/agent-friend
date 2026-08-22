from memory_eval.adapters import case_fragments
from memory_eval.datasets import load_semantic_change_cases


def test_semantic_change_slice_covers_add_retract_merge_and_split() -> None:
    cases = load_semantic_change_cases()
    assert [case.sample_id for case in cases] == [
        "semantic-add",
        "semantic-retract",
        "semantic-many-to-one",
        "semantic-one-to-many",
        "semantic-context-assistant-correction",
    ]
    assert [len(case_fragments(case)) for case in cases] == [1, 2, 3, 2, 2]
    assert {case.questions[0].expected_memory_layer for case in cases} == {
        "semantic",
        "episodic",
    }


def test_semantic_change_slice_is_deterministic_and_uses_holdout_entities() -> None:
    cases = load_semantic_change_cases(limit_samples=2)
    assert [case.sample_id for case in cases] == ["semantic-add", "semantic-retract"]
    rendered = "\n".join(turn.text for case in cases for turn in case.turns)
    assert "青柠" in rendered
    assert "合唱团" in rendered
    assert "游泳" not in rendered


def test_contextual_semantic_change_case_freezes_prior_complete_turn() -> None:
    case = load_semantic_change_cases()[-1]
    fragments = case_fragments(case)

    assert fragments[0].semantic_update_context is None
    assert fragments[1].semantic_update_context is not None
    assert fragments[1].semantic_update_context.source_refs == (
        "semantic-context-assistant-correction#semantic-context-assistant-correction#0-0",
        "semantic-context-assistant-correction#semantic-context-assistant-correction#0-1",
    )
    question = case.questions[0]
    assert question.forbidden_anchors == ["上下文工程指南"]

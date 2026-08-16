from memory_eval.adapters import case_fragments
from memory_eval.datasets import load_semantic_change_cases


def test_semantic_change_slice_covers_add_retract_merge_and_split() -> None:
    cases = load_semantic_change_cases()
    assert [case.sample_id for case in cases] == [
        "semantic-add",
        "semantic-retract",
        "semantic-many-to-one",
        "semantic-one-to-many",
    ]
    assert [len(case_fragments(case)) for case in cases] == [1, 2, 3, 2]
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

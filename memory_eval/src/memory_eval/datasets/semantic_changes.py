"""Product-native holdout slice for Semantic change extraction.

The entities and activities intentionally differ from extractor prompt examples.
Each session becomes one extraction fragment so corrections can address facts
created by earlier fragments.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .case import EvalCase, EvalQuestion, EvalTurn


def load_semantic_change_cases(*, limit_samples: int | None = None) -> list[EvalCase]:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    raw = [
        (
            "semantic-add",
            [["我收养了一只叫青柠的守宫", "这个名字很可爱"]],
            "用户的守宫叫什么？",
            "青柠",
            ["青柠"],
            "semantic",
        ),
        (
            "semantic-retract",
            [
                ["我每周四都参加合唱团排练", "听起来很充实"],
                ["我已经退出合唱团了，以后不会再参加排练", "明白了"],
            ],
            "用户现在还参加合唱团吗？",
            "已经退出，不再参加",
            ["退出合唱团"],
            "episodic",
        ),
        (
            "semantic-many-to-one",
            [
                ["我习惯在工作日早晨喝咖啡", "记住了"],
                ["我每天上班前都会喝一杯咖啡", "了解"],
                ["前面说得不准确，其实我只在周一和周五早晨喝咖啡", "收到"],
            ],
            "用户通常哪几天早晨喝咖啡？",
            "周一和周五",
            ["周一", "周五"],
            "semantic",
        ),
        (
            "semantic-one-to-many",
            [
                ["我周末通常学习外语", "记住了"],
                ["具体来说，我周六学西班牙语，周日学日语", "很清楚"],
            ],
            "用户周六和周日分别学习什么？",
            "周六学西班牙语，周日学日语",
            ["周六", "西班牙语", "周日", "日语"],
            "semantic",
        ),
    ]
    cases: list[EvalCase] = []
    for sample_id, sessions, question, answer, anchors, expected_layer in raw:
        turns: list[EvalTurn] = []
        for session_index, session in enumerate(sessions):
            for turn_index, text in enumerate(session):
                turns.append(
                    EvalTurn(
                        speaker="用户" if turn_index % 2 == 0 else "AI助手",
                        text=text,
                        ts=base + timedelta(days=session_index),
                        dia_id=f"{sample_id}#{session_index}-{turn_index}",
                        session_index=session_index,
                    )
                )
        cases.append(
            EvalCase(
                sample_id=sample_id,
                speaker_a="用户",
                speaker_b="AI助手",
                turns=turns,
                questions=[
                    EvalQuestion(
                        question=question,
                        answer=answer,
                        category="semantic-change",
                        evidence=[turn.dia_id for turn in turns if turn.speaker == "用户"],
                        anchors=anchors,
                        expected_memory_layer=expected_layer,
                    )
                ],
            )
        )
    return cases[:limit_samples] if limit_samples is not None else cases

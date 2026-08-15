"""Small product-native Core Profile regression slice.

Unlike external long-memory corpora, these cases target the exact schema and
replace/add distinctions introduced by requirement 084.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .case import EvalCase, EvalQuestion, EvalTurn


def load_core_profile_cases(*, limit_samples: int | None = None) -> list[EvalCase]:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    raw = [
        (
            "preferred-name-replace",
            [("user", "以后叫我小陈吧"), ("agent", "好呀"), ("user", "还是改叫我陈老师吧")],
            "你应该怎么称呼用户？",
            "陈老师",
            ["陈老师"],
            "core",
            "user.preferred_name",
        ),
        (
            "multiple-current-residences",
            [("user", "我目前在深圳和香港两边住"), ("agent", "记住了")],
            "用户目前住在哪里？",
            "深圳和香港",
            ["深圳", "香港"],
            "core",
            "user.current_residences",
        ),
        (
            "residence-move",
            [("user", "我目前住在杭州"), ("agent", "好的"), ("user", "我已经搬到上海了")],
            "用户现在住在哪里？",
            "上海",
            ["上海"],
            "core",
            "user.current_residences",
        ),
        (
            "agent-nickname",
            [("user", "以后我叫你小月吧"), ("agent", "我喜欢这个称呼")],
            "用户怎么称呼这个 AI 朋友？",
            "小月",
            ["小月"],
            "core",
            "relationship.agent_nickname",
        ),
        (
            "relationship-kind",
            [("user", "你是我最信任的知心朋友"), ("agent", "我会珍惜这份信任")],
            "用户如何定义与这个 AI 朋友的关系？",
            "知心朋友",
            ["知心朋友"],
            "core",
            "relationship.kind",
        ),
        (
            "employer-is-ordinary",
            [("user", "我目前在字节跳动工作"), ("agent", "了解")],
            "用户目前在哪里工作？",
            "字节跳动",
            ["字节跳动"],
            "semantic",
            None,
        ),
    ]
    cases: list[EvalCase] = []
    for sample_id, dialogue, question, answer, anchors, expected_layer, core_field in raw:
        turns = [
            EvalTurn(
                speaker="用户" if speaker == "user" else "AI助手",
                text=text,
                ts=base + timedelta(days=index // 2),
                dia_id=f"{sample_id}#{index}",
                session_index=index // 2,
            )
            for index, (speaker, text) in enumerate(dialogue)
        ]
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
                        category="core-profile",
                        evidence=[turn.dia_id for turn in turns if turn.speaker == "用户"],
                        anchors=anchors,
                        expected_memory_layer=expected_layer,
                        expected_core_field=core_field,
                        forbidden_memory_layers=(
                            ["semantic"] if expected_layer == "core" else ["core"]
                        ),
                    )
                ],
            )
        )
    return cases[:limit_samples] if limit_samples is not None else cases

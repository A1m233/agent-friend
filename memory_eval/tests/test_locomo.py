"""LoCoMo 加载解析：session 投影、时间戳兜底、qa 归一化、limit。"""

from __future__ import annotations

import json
from pathlib import Path

from memory_eval.datasets import load_locomo

_SAMPLE = [
    {
        "sample_id": "conv-1",
        "conversation": {
            "speaker_a": "Alice",
            "speaker_b": "Bob",
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [
                {"speaker": "Alice", "dia_id": "D1:1", "text": "我养了一只猫叫Tom"},
                {"speaker": "Bob", "dia_id": "D1:2", "text": "好可爱"},
                {"speaker": "Alice", "dia_id": "D1:3", "text": ""},  # 空文本应被跳过
            ],
            "session_2_date_time": "garbage-not-a-date",
            "session_2": [
                {"speaker": "Alice", "dia_id": "D2:1", "text": "Tom是橘猫"},
            ],
        },
        "qa": [
            {"question": "Tom是什么猫", "answer": "橘猫", "category": 4, "evidence": ["D2:1"]},
            {"question": "无答案题", "answer": None, "category": 5, "evidence": []},
            {"question": "", "answer": "x"},  # 空问题应被跳过
        ],
    }
]


def _write(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "locomo.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_parses_sessions_and_qa(tmp_path: Path) -> None:
    cases = load_locomo(_write(tmp_path, _SAMPLE))
    assert len(cases) == 1
    case = cases[0]
    assert case.sample_id == "conv-1"
    assert case.speaker_a == "Alice"
    assert case.speaker_b == "Bob"

    # 空文本 turn 被跳过：session_1 2 条 + session_2 1 条 = 3
    assert len(case.turns) == 3
    assert [t.dia_id for t in case.turns] == ["D1:1", "D1:2", "D2:1"]


def test_session_timestamp_parsed_and_fallback(tmp_path: Path) -> None:
    case = load_locomo(_write(tmp_path, _SAMPLE))[0]
    s1 = case.turns[0]
    assert s1.session_index == 1
    assert (s1.ts.year, s1.ts.month, s1.ts.day) == (2023, 5, 8)
    # session_2 时间戳不可解析 → 合成时间（仍带时区，且与 s1 不同）
    s2 = case.turns[2]
    assert s2.session_index == 2
    assert s2.ts.tzinfo is not None
    assert s2.ts != s1.ts


def test_qa_normalization(tmp_path: Path) -> None:
    case = load_locomo(_write(tmp_path, _SAMPLE))[0]
    # 空问题被跳过 → 2 题
    assert len(case.questions) == 2
    q0 = case.questions[0]
    assert q0.answer == "橘猫"
    assert q0.category == "single-hop"  # 数字码 4 → 标签
    assert q0.evidence == ["D2:1"]
    q1 = case.questions[1]
    assert q1.answer == ""  # None → 空串
    assert q1.evidence == []


def test_limit_samples(tmp_path: Path) -> None:
    data = _SAMPLE * 3
    cases = load_locomo(_write(tmp_path, data), limit_samples=2)
    assert len(cases) == 2

"""PerLTQA 加载解析：dialogues 投影、说话人拆分、时间序、Reference Memory 解析。"""

from __future__ import annotations

import json
from pathlib import Path

from memory_eval.datasets import load_perltqa

_MEM = [
    {
        "profile": {"Protagonist": "王小明"},
        "dialogues": {
            "1_0_0#0": {
                "events": "1_0_0",
                "contents": {
                    "2022-05-12 09:00": [
                        "AI助手:你好，王小明",
                        "王小明:我养了一只猫叫Tom",
                    ],
                    "2022-05-12 08:00": [
                        "AI助手:早上好",
                    ],
                },
            }
        },
        # 结构化记忆不应被取用
        "events": {"1_0_0": {"content": "x", "summary": "y"}},
    },
    {
        "profile": {"Protagonist": "无QA的人"},
        "dialogues": {"9_0_0#0": {"events": "9", "contents": {"2022-01-01 00:00": ["AI助手:hi"]}}},
    },
]

_QA = [
    {
        "王小明": {
            "profile": [{"Question": "性别?", "Answer": "男", "Reference Memory": "Gender"}],
            "dialogues": {
                "1_0_0#0": [
                    {
                        "Question": "Tom是什么",
                        "Answer": "一只猫",
                        "Reference Memory": "['1_0_0#0']",
                        "Memory Anchors": [{"Tom": [1, 2]}],
                    }
                ]
            },
        }
    }
]


def _write(tmp_path: Path) -> tuple[Path, Path]:
    mem_path = tmp_path / "perltmem.json"
    qa_path = tmp_path / "perltqa.json"
    mem_path.write_text(json.dumps(_MEM), encoding="utf-8")
    qa_path.write_text(json.dumps(_QA), encoding="utf-8")
    return mem_path, qa_path


def test_loads_only_characters_with_dialogue_qa(tmp_path: Path) -> None:
    mem_path, qa_path = _write(tmp_path)
    cases = load_perltqa(mem_path, qa_path)
    # 只有「王小明」既有对话又有 dialogues QA；「无QA的人」被跳过
    assert len(cases) == 1
    assert cases[0].sample_id == "王小明"
    assert cases[0].speaker_a == "王小明"
    assert cases[0].speaker_b == "AI助手"


def test_turns_sorted_by_time_and_split(tmp_path: Path) -> None:
    mem_path, qa_path = _write(tmp_path)
    case = load_perltqa(mem_path, qa_path)[0]
    # 08:00 的发言应排在 09:00 之前
    assert [t.text for t in case.turns] == ["早上好", "你好，王小明", "我养了一只猫叫Tom"]
    assert case.turns[0].speaker == "AI助手"
    assert case.turns[-1].speaker == "王小明"
    assert case.turns[-1].dia_id == "1_0_0#0"


def test_dialogue_questions_only(tmp_path: Path) -> None:
    mem_path, qa_path = _write(tmp_path)
    case = load_perltqa(mem_path, qa_path)[0]
    # 只取 dialogues 类问题，profile 问题不计入
    assert len(case.questions) == 1
    q = case.questions[0]
    assert q.question == "Tom是什么"
    assert q.category == "dialogues"
    assert q.evidence == ["1_0_0#0"]  # 字符串化列表被解析


def test_limit_samples(tmp_path: Path) -> None:
    mem_path, qa_path = _write(tmp_path)
    cases = load_perltqa(mem_path, qa_path, limit_samples=0)
    assert cases == []

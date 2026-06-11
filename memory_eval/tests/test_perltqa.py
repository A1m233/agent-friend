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
    assert q.anchors == ["Tom"]  # Memory Anchors 提取出 token，丢弃 span


def test_limit_samples(tmp_path: Path) -> None:
    mem_path, qa_path = _write(tmp_path)
    cases = load_perltqa(mem_path, qa_path, limit_samples=0)
    assert cases == []


def test_anchor_parsing_defensive(tmp_path: Path) -> None:
    """anchor 解析的脏数据防御：缺字段 / 错类型 / 错元素 / 多键 / 空 key 均不抛。"""
    mem = [
        {
            "profile": {"Protagonist": "X"},
            "dialogues": {
                "k#0": {
                    "events": "k",
                    "contents": {"2022-05-12 09:00": ["X:hi", "AI助手:hi"]},
                }
            },
        }
    ]
    qa = [
        {
            "X": {
                "dialogues": {
                    "k#0": [
                        {"Question": "q1", "Answer": "a", "Memory Anchors": [{"good": [0, 1]}]},
                        {"Question": "q2", "Answer": "a"},  # 字段缺失
                        {"Question": "q3", "Answer": "a", "Memory Anchors": "not a list"},
                        {  # 多键 dict：取第一个（dict 保持插入顺序）
                            "Question": "q4",
                            "Answer": "a",
                            "Memory Anchors": [{"first": [0, 1], "second": [2, 3]}],
                        },
                        {  # 混合脏数据：空 dict / 空白 key / 字符串元素 / 有效项
                            "Question": "q5",
                            "Answer": "a",
                            "Memory Anchors": [{}, {"   ": [0, 1]}, "string", {"valid": [0, 1]}],
                        },
                    ]
                }
            }
        }
    ]
    mem_path = tmp_path / "perltmem.json"
    qa_path = tmp_path / "perltqa.json"
    mem_path.write_text(json.dumps(mem), encoding="utf-8")
    qa_path.write_text(json.dumps(qa), encoding="utf-8")
    case = load_perltqa(mem_path, qa_path)[0]
    anchors_by_q = {q.question: q.anchors for q in case.questions}
    assert anchors_by_q["q1"] == ["good"]
    assert anchors_by_q["q2"] == []  # 字段缺失 → 默认空列表
    assert anchors_by_q["q3"] == []  # 错类型 → 空
    assert anchors_by_q["q4"] == ["first"]  # 多键取首个
    assert anchors_by_q["q5"] == ["valid"]  # 脏元素跳过、有效项保留

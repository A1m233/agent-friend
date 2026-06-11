"""PerLTQA 数据集加载（**仅 dialogues 子集**）：中文长期记忆对话 → 统一 :class:`EvalCase`。

数据来源 <https://github.com/Elvin-Yiming-Du/PerLTQA>（``Dataset/zh/``，CC BY-NC 4.0，
仅限非商用研究）。两份文件配套：

- ``perltmem.json``：记忆库，``list`` of 角色。每个角色含 ``profile`` / ``social_relationship``
  / ``events`` / ``dialogues``。其中 **只有 ``dialogues`` 是对话流形态**，契合 ``memory``
  "从对话抽取 → 召回" 的范式；profile/关系/events 是结构化记忆，没有对话来源，本 loader 不取。
- ``perltqa.json``：QA，``list`` of ``{角色名: {profile/social_relationship/events/dialogues}}``。
  同样**只取 ``dialogues`` 类问题**。

格式细节：

- ``perltmem`` 的 ``dialogues``：``dict`` of ``"<event>#<n>" -> {"events": ..., "contents":
  {"<时间戳>": ["说话人:正文", ...]}}``。时间戳形如 ``2022-05-12 08:00``。
- ``perltqa`` 的 ``dialogues``：``dict`` of ``"<event>#<n>" -> [ {Question, Answer,
  "Reference Memory", "Memory Anchors"}, ... ]``。``Reference Memory`` 是**字符串化的列表**
  （如 ``"['4_0_0#0']"``）。

角色映射由 adapter 决定：本 loader 把 protagonist 名放进 ``EvalCase.speaker_a``、把
``"AI助手"`` 放进 ``speaker_b``，adapter 据此把 protagonist 映射为 user、其余为 agent。
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .case import EvalCase, EvalQuestion, EvalTurn

__all__ = ["AI_SPEAKER", "load_perltqa"]

AI_SPEAKER = "AI助手"
"""PerLTQA 对话里 AI 一方的固定说话人名。"""

_CATEGORY_DIALOGUES = "dialogues"
_DATE_FMT = "%Y-%m-%d %H:%M"
_FALLBACK_BASE = datetime(2022, 1, 1, tzinfo=UTC)


def _parse_dt(raw: str, ordinal: int) -> datetime:
    try:
        return datetime.strptime(raw.strip(), _DATE_FMT).replace(tzinfo=UTC)
    except ValueError:
        return _FALLBACK_BASE + timedelta(minutes=ordinal)


def _split_line(line: str) -> tuple[str, str]:
    """把 ``"说话人:正文"`` 拆成 ``(speaker, text)``；兼容中文/英文冒号，无冒号则归为正文。"""
    for sep in (":", "："):
        idx = line.find(sep)
        if idx != -1:
            return line[:idx].strip(), line[idx + 1 :].strip()
    return "", line.strip()


def _parse_evidence(raw: object) -> list[str]:
    """``Reference Memory`` 多为字符串化列表（``"['4_0_0#0']"``），尽力解析成 ``list[str]``。"""
    if isinstance(raw, list):
        return [str(e) for e in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return [raw.strip()]
        if isinstance(parsed, list):
            return [str(e) for e in parsed]
        return [str(parsed)]
    return []


def _parse_anchors(raw: object) -> list[str]:
    """``Memory Anchors`` 形如 ``[{"建议": [254, 255]}, ...]``，提取 anchor token、丢弃 span。

    每个元素是单键 dict（key 是 anchor token，value 是 [start, end] 字符 span）。判分只
    需要 token，span 丢弃。脏数据（非 list / 非 dict / 空 key）单条跳过，整体不抛——与
    本 loader 其它解析的防御式风格一致。
    """
    if not isinstance(raw, list):
        return []
    tokens: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        for key in item:
            if isinstance(key, str) and key.strip():
                tokens.append(key.strip())
                break
    return tokens


def _protagonist(mem_entry: dict[str, Any]) -> str | None:
    profile = mem_entry.get("profile")
    if isinstance(profile, dict):
        name = profile.get("Protagonist")
        if isinstance(name, str) and name:
            return name
    return None


def _parse_turns(dialogues: object) -> list[EvalTurn]:
    """把一个角色的全部 ``dialogues`` 投影成按时间序的 :class:`EvalTurn` 列表。

    每个「时间戳对话块」作为一个独立单元（``session_index`` 递增），adapter 据此把每块
    投影成一个 :class:`ConversationFragment` 单独抽取——避免把整个角色的对话塞进一次
    巨大的抽取调用（与 LoCoMo 按 session 切分的粒度对齐）。
    """
    if not isinstance(dialogues, dict):
        return []

    blocks: list[tuple[datetime, str, list[Any]]] = []  # (ts, dialogue_key, lines)
    ordinal = 0
    for dialogue_key, payload in dialogues.items():
        if not isinstance(payload, dict):
            continue
        contents = payload.get("contents")
        if not isinstance(contents, dict):
            continue
        for ts_raw, lines in contents.items():
            if not isinstance(lines, list):
                continue
            blocks.append((_parse_dt(str(ts_raw), ordinal), str(dialogue_key), lines))
            ordinal += 1

    blocks.sort(key=lambda block: block[0])
    turns: list[EvalTurn] = []
    for session_index, (ts, dialogue_key, lines) in enumerate(blocks):
        for line in lines:
            if not isinstance(line, str) or not line.strip():
                continue
            speaker, text = _split_line(line)
            if not text:
                continue
            turns.append(
                EvalTurn(
                    speaker=speaker,
                    text=text,
                    ts=ts,
                    dia_id=dialogue_key,
                    session_index=session_index,
                )
            )
    return turns


def _parse_questions(dialogues_qa: object) -> list[EvalQuestion]:
    """把一个角色的 dialogues 类 QA（``dict`` of ``key -> list[QA]``）展平成问题列表。"""
    if not isinstance(dialogues_qa, dict):
        return []
    questions: list[EvalQuestion] = []
    for items in dialogues_qa.values():
        if not isinstance(items, list):
            continue
        for raw in items:
            if not isinstance(raw, dict):
                continue
            question = str(raw.get("Question", "")).strip()
            if not question:
                continue
            answer = raw.get("Answer")
            questions.append(
                EvalQuestion(
                    question=question,
                    answer="" if answer is None else str(answer),
                    category=_CATEGORY_DIALOGUES,
                    evidence=_parse_evidence(raw.get("Reference Memory")),
                    anchors=_parse_anchors(raw.get("Memory Anchors")),
                )
            )
    return questions


def load_perltqa(
    mem_path: Path | str,
    qa_path: Path | str,
    *,
    limit_samples: int | None = None,
) -> list[EvalCase]:
    """读取 PerLTQA 中文记忆库 + QA，取 **dialogues 子集** 归一成 :class:`EvalCase` 列表。

    Args:
        mem_path: ``perltmem.json`` 路径（记忆库，提供对话素材）。
        qa_path: ``perltqa.json`` 路径（提供 dialogues 类问题）。
        limit_samples: 只取前 N 个角色；``None`` 取全部（以 QA 文件里的角色为准）。

    Returns:
        每个角色一个 :class:`EvalCase`（无对话或无 dialogues 问题的角色被跳过）。

    Raises:
        FileNotFoundError: 路径不存在。
        ValueError: 文件顶层不是 JSON 数组。
    """
    mem_data: Any = json.loads(Path(mem_path).read_text(encoding="utf-8"))
    qa_data: Any = json.loads(Path(qa_path).read_text(encoding="utf-8"))
    if not isinstance(mem_data, list):
        raise ValueError(f"perltmem 应为 JSON 数组，实际为 {type(mem_data).__name__}")
    if not isinstance(qa_data, list):
        raise ValueError(f"perltqa 应为 JSON 数组，实际为 {type(qa_data).__name__}")
    if limit_samples is not None and limit_samples <= 0:
        return []

    mem_by_name: dict[str, dict[str, Any]] = {}
    for entry in mem_data:
        if not isinstance(entry, dict):
            continue
        name = _protagonist(entry)
        if name is not None:
            mem_by_name[name] = entry

    cases: list[EvalCase] = []
    for entry in qa_data:
        if not isinstance(entry, dict):
            continue
        for name, cats in entry.items():
            mem_entry = mem_by_name.get(name)
            if mem_entry is None or not isinstance(cats, dict):
                continue
            turns = _parse_turns(mem_entry.get("dialogues"))
            questions = _parse_questions(cats.get("dialogues"))
            if not turns or not questions:
                continue
            cases.append(
                EvalCase(
                    sample_id=name,
                    speaker_a=name,
                    speaker_b=AI_SPEAKER,
                    turns=turns,
                    questions=questions,
                )
            )
            if limit_samples is not None and len(cases) >= limit_samples:
                return cases
    return cases

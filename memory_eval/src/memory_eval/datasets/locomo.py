"""LoCoMo 数据集加载：``locomo10.json`` → 统一 :class:`EvalCase`。

数据格式见 <https://github.com/snap-research/locomo>（``./data/locomo10.json``）：

- 每个 sample：``sample_id`` + ``conversation`` + ``qa``（及若干 generated/annotated 字段）
- ``conversation``：``speaker_a`` / ``speaker_b`` 两位说话人；``session_<n>`` 列出该
  session 的 turns，``session_<n>_date_time`` 是该 session 的时间戳
- 一个 turn：``speaker`` / ``dia_id`` / ``text``（可能含 ``img_url`` 等多模态字段，v1 忽略）
- ``qa`` 每条：``question`` / ``answer`` / ``category`` / ``evidence``（dia_id 列表）

解析采取防御式：缺字段 / 类型不符的条目跳过而非报错，让 PoC 在脏数据上也能跑。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .case import EvalCase, EvalQuestion, EvalTurn

__all__ = ["load_locomo"]

_SESSION_RE = re.compile(r"^session_(\d+)$")
# LoCoMo 数字类别码 → 人类可读标签（归一到跨基准通用的 EvalQuestion.category）。
_CATEGORY_LABELS = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}
# LoCoMo 时间戳形如 "1:56 pm on 8 May, 2023"。strptime 对 %p/%B 大小写不敏感。
_DATE_FMT = "%I:%M %p on %d %B, %Y"
# 解析失败时的合成基准：按 session 序号偏移天数，保留相对先后供时间衰减打分。
_FALLBACK_BASE = datetime(2023, 1, 1, tzinfo=UTC)


def _parse_dt(raw: object, session_index: int) -> datetime:
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.strptime(raw.strip(), _DATE_FMT).replace(tzinfo=UTC)
        except ValueError:
            pass
    return _FALLBACK_BASE + timedelta(days=session_index)


def _parse_turns(conversation: dict[str, Any]) -> list[EvalTurn]:
    indices = sorted(int(m.group(1)) for k in conversation if (m := _SESSION_RE.match(k)))
    turns: list[EvalTurn] = []
    for idx in indices:
        raw_turns = conversation.get(f"session_{idx}")
        if not isinstance(raw_turns, list):
            continue
        ts = _parse_dt(conversation.get(f"session_{idx}_date_time"), idx)
        for raw in raw_turns:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            turns.append(
                EvalTurn(
                    speaker=str(raw.get("speaker", "")),
                    text=text,
                    ts=ts,
                    dia_id=str(raw.get("dia_id", "")),
                    session_index=idx,
                )
            )
    return turns


def _parse_questions(qa: list[Any]) -> list[EvalQuestion]:
    questions: list[EvalQuestion] = []
    for raw in qa:
        if not isinstance(raw, dict):
            continue
        question = str(raw.get("question", "")).strip()
        if not question:
            continue
        answer = raw.get("answer")
        category = raw.get("category")
        evidence = raw.get("evidence")
        questions.append(
            EvalQuestion(
                question=question,
                answer="" if answer is None else str(answer),
                category=_CATEGORY_LABELS.get(category) if isinstance(category, int) else None,
                evidence=[str(e) for e in evidence] if isinstance(evidence, list) else [],
            )
        )
    return questions


def load_locomo(path: Path | str, *, limit_samples: int | None = None) -> list[EvalCase]:
    """读取 LoCoMo JSON，归一化成 :class:`EvalCase` 列表。

    Args:
        path: ``locomo10.json`` 路径。
        limit_samples: 只取前 N 个 conversation（控制评测成本）；``None`` 取全部。

    Returns:
        归一化后的样本列表（无法解析的样本被跳过）。

    Raises:
        FileNotFoundError: 路径不存在。
        ValueError: 顶层不是 JSON 数组。
    """
    data: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"LoCoMo 数据应为 JSON 数组，实际为 {type(data).__name__}")
    if limit_samples is not None and limit_samples <= 0:
        return []

    cases: list[EvalCase] = []
    for sample in data:
        if not isinstance(sample, dict):
            continue
        conversation = sample.get("conversation")
        if not isinstance(conversation, dict):
            continue
        qa = sample.get("qa")
        cases.append(
            EvalCase(
                sample_id=str(sample.get("sample_id", f"sample-{len(cases)}")),
                speaker_a=str(conversation.get("speaker_a", "speaker_a")),
                speaker_b=str(conversation.get("speaker_b", "speaker_b")),
                turns=_parse_turns(conversation),
                questions=_parse_questions(qa if isinstance(qa, list) else []),
            )
        )
        if limit_samples is not None and len(cases) >= limit_samples:
            break
    return cases

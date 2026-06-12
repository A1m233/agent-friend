"""``Extractor``：用 LLM 从一段对话 fragment 抽出 episodic 摘要 + semantic 操作。

复用注入的 :class:`llm_providers.LLMClient`（design §3.6：v1 与主对话同 client，
构造可换独立 client）。本组件**只负责"调 LLM + 解析"**，不碰存储——落库由
:class:`Reconciler` 负责。

详见 docs/requirements/008-engine-memory/design.md §5.2。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .result import ExtractionOutput, SemanticOp

if TYPE_CHECKING:
    from llm_providers import LLMClient

    from ..contracts import ConversationFragment
    from ..store import SemanticRow

__all__ = ["Extractor"]

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.md"
_MAX_FACTS_IN_PROMPT = 60
"""喂给 LLM 的已知事实条数上限，控制 prompt 体积。"""


class Extractor:
    """对话 fragment → :class:`ExtractionOutput` 的 LLM 抽取器。

    Args:
        llm_client: 抽取用的 LLM 客户端。
        prompt: 可选，覆盖默认抽取 system prompt（测试 / 调参用）。
    """

    def __init__(self, llm_client: LLMClient, *, prompt: str | None = None) -> None:
        self._llm = llm_client
        self._prompt = prompt if prompt is not None else _PROMPT_PATH.read_text(encoding="utf-8")

    def extract(
        self, fragment: ConversationFragment, existing_facts: list[SemanticRow]
    ) -> ExtractionOutput:
        """抽取一个 fragment。

        失败（LLM 报错 / 返回非法 JSON）时**返回空 output、不抛异常**——抽取是
        尽力而为的旁路，不应让 worker 崩溃或污染对话。
        """
        user_content = self._render_input(fragment, existing_facts)
        messages = [
            {"role": "system", "content": self._prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            raw = self._llm.complete(messages)
        except Exception:
            logger.warning("记忆抽取 LLM 调用失败，跳过本段", exc_info=True)
            return ExtractionOutput()

        return _parse_output(raw)

    def _render_input(
        self, fragment: ConversationFragment, existing_facts: list[SemanticRow]
    ) -> str:
        lines: list[str] = []
        if existing_facts:
            lines.append("## 已知事实清单")
            for f in existing_facts[:_MAX_FACTS_IN_PROMPT]:
                lines.append(f"- {f.statement}")
            lines.append("")
        else:
            lines.append("## 已知事实清单\n（暂无）\n")
        lines.append("## 对话片段")
        for u in fragment.utterances:
            who = "用户" if u.speaker == "user" else "AI"
            lines.append(f"{who}: {u.text}")
        return "\n".join(lines)


def _parse_output(raw: str) -> ExtractionOutput:
    """把 LLM 文本解析成 :class:`ExtractionOutput`，尽量鲁棒。

    兼容新旧 prompt 输出（pass-1）：

    - 新字段 ``episodic_entries: list[str]``（推荐）
    - 旧字段 ``episodic_summary: str | None``——单元素 list 处理（向下兼容）
    """
    text = _strip_code_fence(raw).strip()
    if not text:
        return ExtractionOutput()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("记忆抽取返回非法 JSON，跳过：%s", text[:200])
        return ExtractionOutput()
    if not isinstance(obj, dict):
        return ExtractionOutput()

    entries = _parse_entries(obj)

    ops: list[SemanticOp] = []
    raw_ops = obj.get("semantic_ops")
    if isinstance(raw_ops, list):
        for item in raw_ops:
            op = _parse_op(item)
            if op is not None:
                ops.append(op)
    return ExtractionOutput(episodic_entries=entries, semantic_ops=ops)


def _parse_entries(obj: dict[str, object]) -> list[str]:
    """从 LLM JSON 抽取 episodic 条目，兼容新旧字段。

    新字段优先：``episodic_entries: list[str]``。旧字段 fallback：
    ``episodic_summary: str | null``——非空字符串当单元素 list；其他都视为空。
    """
    entries_raw = obj.get("episodic_entries")
    if isinstance(entries_raw, list):
        cleaned = [e.strip() for e in entries_raw if isinstance(e, str) and e.strip()]
        if cleaned:
            return cleaned

    summary = obj.get("episodic_summary")
    if isinstance(summary, str) and summary.strip():
        return [summary.strip()]

    return []


def _parse_op(item: object) -> SemanticOp | None:
    if not isinstance(item, dict):
        return None
    statement = item.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        return None
    op = item.get("op")
    if op not in ("add", "supersede"):
        op = "add"
    importance = item.get("importance", 0.5)
    if not isinstance(importance, int | float):
        importance = 0.5
    importance = max(0.0, min(1.0, float(importance)))
    pinned = bool(item.get("pinned", False))
    speaker_origin = item.get("speaker_origin", "user")
    if speaker_origin not in ("user", "agent"):
        speaker_origin = "user"
    target_hint = item.get("target_hint")
    if not isinstance(target_hint, str) or not target_hint.strip():
        target_hint = None
    return SemanticOp(
        op=op,  # type: ignore[arg-type]
        statement=statement.strip(),
        importance=importance,
        pinned=pinned,
        speaker_origin=speaker_origin,
        target_hint=target_hint,
    )


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 围栏（如果模型不听话加了）。"""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)

"""``Reconciler``：把 :class:`ExtractionOutput` 落库（独立组件，便于迭代冲突策略）。

职责：
- 写 episodic 摘要（带 ``source_ref`` 指针 + 时间）。
- 逐条处理 semantic 操作：``add`` 直接写；``supersede`` 先把命中的旧事实置
  ``valid_until``、再写新值（定位不到就降级为 add）。
- 落库时应用"user-said 加一档重要性"（design §5.3）。

**冲突解决只做"取代"，不做实体消歧 / 合并**（design §1.2）。要升级策略只改本文件。

详见 docs/requirements/008-engine-memory/design.md §5.2。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from ..store import EpisodicRow, SemanticRow
from .result import ExtractionOutput, ExtractionResult, normalize_memory_metadata

if TYPE_CHECKING:
    from ..contracts import ConversationFragment

logger = logging.getLogger(__name__)

__all__ = ["Reconciler"]

_USER_SAID_BONUS = 0.15
"""user-said 相对 agent-said 的重要性加成（design §5.3）。"""


class Reconciler:
    """把抽取产物落进 :class:`SqliteMemoryStore`。

    Args:
        store: 目标记忆库（需有 add_semantic / supersede_semantic / add_episodic）。
        user_said_bonus: user 来源事实的重要性加成。
    """

    def __init__(
        self,
        store: object,
        *,
        user_said_bonus: float = _USER_SAID_BONUS,
        normalize_metadata: bool = True,
    ) -> None:
        # store 用 duck typing：真实类型 SqliteMemoryStore；测试可注入 fake。
        self._store = store
        self._bonus = user_said_bonus
        self._normalize_metadata = normalize_metadata

    def apply(
        self,
        output: ExtractionOutput,
        fragment: ConversationFragment,
        existing_facts: list[SemanticRow],
    ) -> ExtractionResult:
        now = datetime.now(UTC)
        episodic_ids: list[str] = []
        provenance: list[str] = []

        if output.episodic_entries:
            occurred_at = fragment.utterances[-1].ts if fragment.utterances else now
            source_ref = _fragment_source_ref(fragment)
            for entry in output.episodic_entries:
                ep_id = str(uuid4())
                self._store.add_episodic(  # type: ignore[attr-defined]
                    EpisodicRow(
                        id=ep_id,
                        summary=entry,
                        source_ref=source_ref,
                        persona_id=fragment.persona_id,
                        occurred_at=occurred_at,
                        created_at=now,
                        importance=0.5,
                        owner_user_id=fragment.owner_user_id,
                    )
                )
                episodic_ids.append(ep_id)
            provenance = list(episodic_ids)

        added: list[str] = []
        superseded: list[str] = []

        for op in output.semantic_ops:
            if self._normalize_metadata:
                memory_class, fact_key, pinned = normalize_memory_metadata(
                    op.statement,
                    pinned=op.pinned,
                    memory_class=getattr(op, "memory_class", "normal"),
                    fact_key=getattr(op, "fact_key", None),
                )
            else:
                memory_class, fact_key, pinned = "normal", None, op.pinned
            match: SemanticRow | None = None
            if fact_key is not None and hasattr(self._store, "semantic_by_fact_key"):
                match = self._store.semantic_by_fact_key(
                    fact_key,
                    owner_user_id=fragment.owner_user_id,
                )
            if match is None and op.op == "supersede" and op.target_hint:
                match = _find_match(op.target_hint, existing_facts)
            if match is None and op.op == "supersede" and op.target_hint:
                logger.warning(
                    "supersede hint unmatched hint=%s new=%s; downgrade to add",
                    op.target_hint,
                    op.statement,
                )

            if match is not None and match.statement.strip() == op.statement.strip():
                continue

            importance = op.importance
            if op.speaker_origin == "user":
                importance = min(1.0, importance + self._bonus)

            row = SemanticRow(
                id=str(uuid4()),
                statement=op.statement,
                persona_id=fragment.persona_id,
                created_at=now,
                updated_at=now,
                importance=importance,
                pinned=pinned,
                source="extracted",
                speaker_origin=op.speaker_origin,
                valid_from=now,
                provenance=list(provenance),
                owner_user_id=fragment.owner_user_id,
                memory_class=memory_class,
                fact_key=fact_key,
                supersedes_id=match.id if match is not None else None,
            )
            replaced = False
            if match is not None:
                if hasattr(self._store, "supersede_and_add_semantic"):
                    replaced = self._store.supersede_and_add_semantic(
                        match.id,
                        row,
                        now,
                    )
                else:
                    replaced = self._store.supersede_semantic(  # type: ignore[attr-defined]
                        match.id,
                        now,
                    )
                    if replaced:
                        self._store.add_semantic(row)  # type: ignore[attr-defined]
                if replaced:
                    superseded.append(match.statement)
                    logger.warning("superseded fact=%s new=%s", match.statement, op.statement)
            if not replaced:
                if match is not None:
                    row.supersedes_id = None
                self._store.add_semantic(row)  # type: ignore[attr-defined]
            added.append(op.statement)

        return ExtractionResult(
            session_id=fragment.session_id,
            episodic_ids=episodic_ids,
            added_semantic=added,
            superseded_semantic=superseded,
        )


def _fragment_source_ref(fragment: ConversationFragment) -> str:
    """拼 episodic 的 source_ref：``"{session_id}#{first_uuid}..{last_uuid}"``。"""
    if not fragment.utterances:
        return f"{fragment.session_id}#"
    first = _uuid_of(fragment.utterances[0].source_ref)
    last = _uuid_of(fragment.utterances[-1].source_ref)
    return f"{fragment.session_id}#{first}..{last}"


def _uuid_of(source_ref: str) -> str:
    """从 ``"{session_id}#{uuid}"`` 取 uuid 段。"""
    _, _, tail = source_ref.partition("#")
    return tail


def _find_match(hint: str, facts: list[SemanticRow]) -> SemanticRow | None:
    """按 target_hint 在已知事实里找被取代项：精确 > 互相包含（v1 粗匹配）。"""
    hint_norm = hint.strip()
    for f in facts:
        if f.statement.strip() == hint_norm:
            return f
    for f in facts:
        s = f.statement.strip()
        if hint_norm and (hint_norm in s or s in hint_norm):
            return f
    return None

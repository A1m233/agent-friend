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
from .result import ExtractionOutput, ExtractionResult

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

    def __init__(self, store: object, *, user_said_bonus: float = _USER_SAID_BONUS) -> None:
        # store 用 duck typing：真实类型 SqliteMemoryStore；测试可注入 fake。
        self._store = store
        self._bonus = user_said_bonus

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
            if op.op == "supersede" and op.target_hint:
                match = _find_match(op.target_hint, existing_facts)
                if match is not None:
                    if self._store.supersede_semantic(match.id, now):  # type: ignore[attr-defined]
                        superseded.append(match.statement)
                        logger.warning("superseded fact=%s new=%s", match.statement, op.statement)
                    else:
                        logger.warning(
                            "supersede race missed fact=%s new=%s", match.statement, op.statement
                        )
                else:
                    logger.warning(
                        "supersede hint unmatched hint=%s new=%s; downgrade to add",
                        op.target_hint,
                        op.statement,
                    )

            importance = op.importance
            if op.speaker_origin == "user":
                importance = min(1.0, importance + self._bonus)

            self._store.add_semantic(  # type: ignore[attr-defined]
                SemanticRow(
                    id=str(uuid4()),
                    statement=op.statement,
                    persona_id=fragment.persona_id,
                    created_at=now,
                    updated_at=now,
                    importance=importance,
                    pinned=op.pinned,
                    source="extracted",
                    speaker_origin=op.speaker_origin,
                    valid_from=now,
                    provenance=list(provenance),
                    owner_user_id=fragment.owner_user_id,
                )
            )
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

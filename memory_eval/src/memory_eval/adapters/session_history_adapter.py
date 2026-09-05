"""把 :class:`EvalCase` 投影成生产 ``SessionHistorySource`` 可读的只读 store。"""

from __future__ import annotations

from dataclasses import dataclass

from agent.memory_search import SessionHistorySource
from agent.sessions.events import SCHEMA_VERSION, Event
from agent.sessions.session import Session
from agent.sessions.store import SessionSummary

from memory_eval.datasets import EvalCase, EvalTurn

from .memory_adapter import BENCHMARK_PERSONA_ID

__all__ = ["case_history_source"]


@dataclass(frozen=True)
class _CaseSession:
    session: Session
    summary: SessionSummary


class _EvalCaseSessionStore:
    """只读 SessionStore；评测不得把 case 历史写回数据集。"""

    def __init__(self, sessions: list[_CaseSession]) -> None:
        self._sessions = {item.session.session_id: item for item in sessions}

    def list(self) -> list[SessionSummary]:
        return sorted(
            (item.summary for item in self._sessions.values()),
            key=lambda item: item.updated_at,
            reverse=True,
        )

    def exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    def load(self, session_id: str) -> Session:
        return self._sessions[session_id].session

    def create(self, session: Session) -> None:
        raise RuntimeError("评测 SessionStore 为只读")

    def append_event(self, session_id: str, event: Event) -> None:
        raise RuntimeError("评测 SessionStore 为只读")

    def delete(self, session_id: str) -> None:
        raise RuntimeError("评测 SessionStore 为只读")

    def latest(self) -> SessionSummary | None:
        summaries = self.list()
        return summaries[0] if summaries else None


def case_history_source(case: EvalCase) -> SessionHistorySource:
    """复用真实 history source，仅把 benchmark turns 适配成 session events。"""
    turns_by_session: dict[int, list[EvalTurn]] = {}
    for turn in case.turns:
        turns_by_session.setdefault(turn.session_index, []).append(turn)

    projected: list[_CaseSession] = []
    for session_index, turns in sorted(turns_by_session.items()):
        turns.sort(key=lambda item: (item.ts, item.dia_id))
        if not turns:
            continue
        session_id = f"{case.sample_id}-session-{session_index}"
        created_at = turns[0].ts
        events = [
            Event(
                type="session_meta",
                uuid=session_id,
                ts=created_at,
                payload={
                    "schema_version": SCHEMA_VERSION,
                    "initial_title": case.sample_id,
                    "initial_persona": BENCHMARK_PERSONA_ID,
                    "initial_model": "memory-eval/no-generation",
                    "initial_persona_id": BENCHMARK_PERSONA_ID,
                },
            )
        ]
        for ordinal, turn in enumerate(turns, start=1):
            events.append(
                Event(
                    type=(
                        "user_message" if turn.speaker == case.speaker_a else "assistant_message"
                    ),
                    uuid=f"{case.sample_id}#{turn.dia_id}#{ordinal}",
                    ts=turn.ts,
                    payload={"content": turn.text},
                    meta={"persona_id": BENCHMARK_PERSONA_ID},
                )
            )
        session = Session.from_events(events)
        projected.append(
            _CaseSession(
                session=session,
                summary=SessionSummary(
                    session_id=session_id,
                    title=case.sample_id,
                    created_at=created_at,
                    updated_at=events[-1].ts,
                    persona=BENCHMARK_PERSONA_ID,
                    model="memory-eval/no-generation",
                ),
            )
        )
    return SessionHistorySource(_EvalCaseSessionStore(projected))

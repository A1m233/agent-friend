"""Short-lived preparation for a synchronous search over frozen history."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..messages import Message
from .usage_anchor import InputBudgetCache


class CandidateBudgetCache:
    """Owned by one candidate assessor, never by a conversation or provider.

    The selector does not mutate its source messages. Strong references prevent
    identity reuse; new messages with the same UUID are deliberately prepared anew.
    Final admission and later compactions use fresh reads and fresh evidence.
    """

    def __init__(self) -> None:
        self.budget = InputBudgetCache()
        self._projected: dict[int, tuple[Message, Message]] = {}
        self._rendered: dict[int, tuple[Message, dict[str, Any]]] = {}

    def project(self, message: Message, projector: Callable[[Message], Message]) -> Message:
        key = id(message)
        if key not in self._projected:
            self._projected[key] = (message, projector(message))
        return self._projected[key][1]

    def render(
        self, message: Message, renderer: Callable[[Message], dict[str, Any]]
    ) -> dict[str, Any]:
        key = id(message)
        if key not in self._rendered:
            self._rendered[key] = (message, renderer(message))
        return self._rendered[key][1]

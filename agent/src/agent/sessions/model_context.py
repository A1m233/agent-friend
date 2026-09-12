"""Fixed append-only model context contributors for one process-local session."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from html import escape
from typing import Literal

from ..messages import Message

_Contributor = Literal[
    "calendar",
    "surface",
    "channel",
    "permission",
    "runtime_capabilities",
    "capability_catalog",
]


@dataclass(frozen=True)
class CalendarState:
    local_date: date
    timezone: str


@dataclass(frozen=True)
class ModelContextSnapshot:
    initialized: frozenset[_Contributor] = frozenset()
    calendar: CalendarState | None = None
    surface: str | None = None
    channel: str | None = None
    approval_mode: str | None = None
    approval_version: int | None = None
    runtime_capabilities: str | None = None
    capability_catalog: str | None = None


@dataclass(frozen=True)
class ModelContextPlan:
    messages: tuple[Message, ...]
    next_snapshot: ModelContextSnapshot


class ModelContextProjection:
    """Complete a candidate history from current host facts, not remembered flags.

    Candidate identities are allocated once per build so budgeting and rendering
    see identical additions. Existing equivalent facts keep their original place.
    """

    def __init__(self, snapshot: ModelContextPlan) -> None:
        self.snapshot = snapshot
        self.deltas = {
            message.uuid: replace(
                message, content=message.content.replace('kind="snapshot"', 'kind="delta"')
            )
            for message in snapshot.messages
        }

    @staticmethod
    def _equivalent(left: Message, right: Message) -> bool:
        return left.role == right.role and left.content.replace(
            'kind="delta"', 'kind="snapshot"'
        ) == right.content.replace('kind="delta"', 'kind="snapshot"')

    def apply(self, messages: list[Message], *, at_end: bool = False) -> list[Message]:
        latest = {
            message.meta.get("model_context_contributor"): message
            for message in messages
            if message.meta.get("trusted_host_context") is True
        }
        additions = []
        for desired in self.snapshot.messages:
            previous = latest.get(desired.meta.get("model_context_contributor"))
            if previous is not None and self._equivalent(previous, desired):
                continue
            additions.append(self.deltas[desired.uuid] if previous is not None else desired)
        if not additions:
            return list(messages)
        index = len(messages)
        if not at_end:
            index = next(
                (
                    index
                    for index in range(len(messages) - 1, -1, -1)
                    if messages[index].meta.get("current_input") is True
                ),
                index,
            )
            # A historical delta after the user input must not override the new
            # current fact. Completed tool results may precede this safe tail.
            if any(message.meta.get("model_context_contributor") for message in messages[index:]):
                index = len(messages)
        return [*messages[:index], *additions, *messages[index:]]

    def accepted_plan(self, messages: list[Message]) -> ModelContextPlan:
        contributors = {
            message.meta.get("model_context_contributor") for message in self.snapshot.messages
        }
        latest = {
            message.meta.get("model_context_contributor"): message
            for message in messages
            if message.meta.get("trusted_host_context") is True
            and message.meta.get("model_context_contributor") in contributors
        }
        return ModelContextPlan(tuple(latest.values()), self.snapshot.next_snapshot)


@dataclass
class SessionModelContext:
    """Compare a fixed set of host facts without introducing a contributor registry."""

    snapshot: ModelContextSnapshot = field(default_factory=ModelContextSnapshot)

    def plan(
        self,
        *,
        current_time: datetime,
        timezone: str,
        surface: str | None,
        surface_context: str,
        channel: str,
        channel_instruction: str | None,
        approval_mode: str | None,
        approval_version: int | None,
        runtime_capabilities: str,
        capability_catalog: str | None,
    ) -> ModelContextPlan:
        """Return the immutable deltas needed at the next model boundary."""

        previous = self.snapshot
        initialized = set(previous.initialized)
        messages: list[Message] = []
        calendar = CalendarState(current_time.date(), timezone)

        if "calendar" not in initialized or previous.calendar != calendar:
            kind = "snapshot" if "calendar" not in initialized else "delta"
            messages.append(
                _context_message(
                    "calendar",
                    "user",
                    (
                        '<agent_friend_environment_context source="host" '
                        f'contributor="calendar" kind="{kind}">\n'
                        f'  <calendar date="{calendar.local_date.isoformat()}" '
                        f'timezone="{escape(calendar.timezone, quote=True)}" />\n'
                        "</agent_friend_environment_context>"
                    ),
                    current_time,
                )
            )
            initialized.add("calendar")

        if "surface" not in initialized or previous.surface != surface:
            kind = "snapshot" if "surface" not in initialized else "delta"
            messages.append(
                _context_message(
                    "surface",
                    "user",
                    (
                        '<agent_friend_environment_context source="host" '
                        f'contributor="surface" kind="{kind}">\n'
                        f"{surface_context}\n"
                        "</agent_friend_environment_context>"
                    ),
                    current_time,
                )
            )
            initialized.add("surface")

        if "channel" not in initialized or previous.channel != channel:
            kind = "snapshot" if "channel" not in initialized else "delta"
            instruction = channel_instruction.strip() if channel_instruction else ""
            body = f"\n{instruction}" if instruction else ""
            messages.append(
                _context_message(
                    "channel",
                    "system",
                    (
                        '<agent_friend_channel_context source="host" '
                        f'kind="{kind}" channel="{escape(channel, quote=True)}">'
                        f"{body}\n"
                        "Only the latest channel context applies; older channel contexts are history.\n"
                        "</agent_friend_channel_context>"
                    ),
                    current_time,
                )
            )
            initialized.add("channel")

        permission = (approval_mode, approval_version)
        previous_permission = (previous.approval_mode, previous.approval_version)
        if approval_mode is not None and (
            "permission" not in initialized or previous_permission != permission
        ):
            kind = "snapshot" if "permission" not in initialized else "delta"
            messages.append(
                _context_message(
                    "permission",
                    "system",
                    (
                        '<agent_friend_permission_context source="host" '
                        f'kind="{kind}" approval_mode="{escape(approval_mode, quote=True)}" '
                        f'version="{approval_version or 0}">\n'
                        "This context helps planning only. The host enforces current permission "
                        "at action time; older permission contexts never grant access.\n"
                        "</agent_friend_permission_context>"
                    ),
                    current_time,
                )
            )
            initialized.add("permission")

        if (
            "runtime_capabilities" not in initialized
            or previous.runtime_capabilities != runtime_capabilities
        ):
            kind = "snapshot" if "runtime_capabilities" not in initialized else "delta"
            messages.append(
                _context_message(
                    "runtime_capabilities",
                    "system",
                    (
                        '<agent_friend_runtime_capabilities source="host" '
                        f'kind="{kind}">\n'
                        f"{runtime_capabilities}\n"
                        "</agent_friend_runtime_capabilities>"
                    ),
                    current_time,
                )
            )
            initialized.add("runtime_capabilities")

        normalized_catalog = capability_catalog.strip() if capability_catalog else None
        catalog_changed = (
            "capability_catalog" in initialized
            and previous.capability_catalog != normalized_catalog
        )
        if normalized_catalog is not None and "capability_catalog" not in initialized:
            catalog_changed = True
        if catalog_changed:
            kind = "snapshot" if "capability_catalog" not in initialized else "delta"
            catalog_body = normalized_catalog or (
                '<capability_catalog available="false" />\n'
                "No callable tool catalog is active on this request."
            )
            messages.append(
                _context_message(
                    "capability_catalog",
                    "system",
                    (
                        '<agent_friend_capability_catalog source="host" '
                        f'kind="{kind}">\n'
                        f"{catalog_body}\n"
                        "</agent_friend_capability_catalog>"
                    ),
                    current_time,
                )
            )
            initialized.add("capability_catalog")

        return ModelContextPlan(
            messages=tuple(messages),
            next_snapshot=ModelContextSnapshot(
                initialized=frozenset(initialized),
                calendar=calendar,
                surface=surface,
                channel=channel,
                approval_mode=approval_mode,
                approval_version=approval_version,
                runtime_capabilities=runtime_capabilities,
                capability_catalog=normalized_catalog,
            ),
        )

    def commit(self, plan: ModelContextPlan) -> None:
        self.snapshot = plan.next_snapshot

    def reset(self) -> None:
        self.snapshot = ModelContextSnapshot()


def _context_message(
    contributor: _Contributor,
    role: Literal["system", "user"],
    content: str,
    timestamp: datetime,
) -> Message:
    return Message(
        role=role,
        content=content,
        timestamp=timestamp,
        meta={
            "model_only": True,
            "trusted_host_context": True,
            "synthetic": f"model_context_{contributor}",
            "model_context_contributor": contributor,
            "prompt_cache_domain": "runtime",
        },
    )


__all__ = [
    "CalendarState",
    "ModelContextPlan",
    "ModelContextSnapshot",
    "SessionModelContext",
]

"""Inbound IM event normalization before routing into the agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from agent.attachments import AttachmentRef

from .content import OutboundContent
from .event import InboundEvent

__all__ = [
    "CompositeInboundResolver",
    "InboundResolveResult",
    "InboundResolver",
    "NoopInboundResolver",
]


@dataclass(frozen=True)
class InboundResolveResult:
    """Decision made before an inbound IM event reaches ``IMRouter``."""

    action: Literal["route", "reply", "ignore"]
    event: InboundEvent | None = None
    reply: OutboundContent | None = None
    reason: str = ""
    attachments: tuple[AttachmentRef, ...] = ()

    @classmethod
    def route(
        cls,
        event: InboundEvent,
        *,
        attachments: tuple[AttachmentRef, ...] = (),
    ) -> InboundResolveResult:
        return cls(action="route", event=event, attachments=attachments)

    @classmethod
    def direct_reply(cls, content: OutboundContent, *, reason: str = "") -> InboundResolveResult:
        return cls(action="reply", reply=content, reason=reason)

    @classmethod
    def ignore(cls, *, reason: str = "") -> InboundResolveResult:
        return cls(action="ignore", reason=reason)


class InboundResolver(Protocol):
    """Protocol for platform-specific inbound event normalization."""

    async def resolve(self, im_type: str, event: InboundEvent) -> InboundResolveResult:
        """Normalize an inbound event before it reaches the router."""


class NoopInboundResolver:
    """Default resolver that preserves current text-only behavior."""

    async def resolve(self, im_type: str, event: InboundEvent) -> InboundResolveResult:
        return InboundResolveResult.route(event)


class CompositeInboundResolver:
    """Run independent inbound normalizers without coupling them together."""

    def __init__(self, *resolvers: InboundResolver) -> None:
        self._resolvers = resolvers

    async def resolve(self, im_type: str, event: InboundEvent) -> InboundResolveResult:
        current = event
        attachments: list[AttachmentRef] = []
        for resolver in self._resolvers:
            result = await resolver.resolve(im_type, current)
            attachments.extend(result.attachments)
            if result.action != "route":
                return result
            current = result.event or current
        return InboundResolveResult.route(current, attachments=tuple(attachments))

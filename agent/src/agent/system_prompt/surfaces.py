"""Trusted interaction-surface identifiers and model-facing turn context."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NewType
from xml.sax.saxutils import quoteattr

InteractionSurfaceId = NewType("InteractionSurfaceId", str)

_INTERNAL_USAGE_INSTRUCTION = (
    "这是只对当前这次模型调用有效的完整内部运行时快照。"
    "只静默使用这些事实来理解和完成当前任务。除非用户直接询问当前交互端，"
    "或当前任务确实依赖该信息，否则不要在回复中提及交互端。"
    "不要告诉用户你收到了这段上下文、系统提示或端信息。"
)

DESKTOP_CHAT_SURFACE = InteractionSurfaceId("desktop_chat")
VOICE_CALL_SURFACE = InteractionSurfaceId("voice_call")
IM_QQ_SURFACE = InteractionSurfaceId("im:qq")


@dataclass(frozen=True)
class InteractionSurfaceContext:
    """Structured attributes exposed to the model for one trusted surface."""

    attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class InteractionContextRenderer:
    """Render a trusted surface as a non-persistent current-turn context packet."""

    surfaces: Mapping[InteractionSurfaceId, InteractionSurfaceContext]

    def render(self, surface: InteractionSurfaceId) -> str:
        return self.render_snapshot(surface=surface)

    def render_snapshot(
        self,
        *,
        surface: InteractionSurfaceId | None,
        runtime_context: str | None = None,
    ) -> str:
        """Render one non-persistent, model-visible interaction snapshot."""

        lines = ["<interaction_context>"]
        if runtime_context:
            lines.extend(f"  {line}" for line in runtime_context.splitlines())
        if surface is None:
            lines.append('  <surface available="false" />')
        else:
            lines.append(f"  {self._render_surface(surface)}")
        lines.append(f"  <usage>{_INTERNAL_USAGE_INSTRUCTION}</usage>")
        lines.append("</interaction_context>")
        return "\n".join(lines)

    def _render_surface(self, surface: InteractionSurfaceId) -> str:
        try:
            context = self.surfaces[surface]
        except KeyError as exc:
            raise ValueError(f"unsupported interaction surface: {surface}") from exc
        attributes = " ".join(f"{name}={quoteattr(value)}" for name, value in context.attributes)
        return f"<surface {attributes} />"


DEFAULT_INTERACTION_CONTEXT_RENDERER = InteractionContextRenderer(
    surfaces={
        DESKTOP_CHAT_SURFACE: InteractionSurfaceContext(attributes=(("type", "desktop_chat"),)),
        VOICE_CALL_SURFACE: InteractionSurfaceContext(attributes=(("type", "voice_call"),)),
        IM_QQ_SURFACE: InteractionSurfaceContext(attributes=(("type", "im"), ("provider", "qq"))),
    }
)

__all__ = [
    "DEFAULT_INTERACTION_CONTEXT_RENDERER",
    "DESKTOP_CHAT_SURFACE",
    "IM_QQ_SURFACE",
    "VOICE_CALL_SURFACE",
    "InteractionContextRenderer",
    "InteractionSurfaceContext",
    "InteractionSurfaceId",
]

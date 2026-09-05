"""Conversation-only phase instructions; internal generate prompts stay separate."""

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def phase_instruction() -> str:
    return (
        (files("agent.prompt_sections") / "assistant_phase.md").read_text(encoding="utf-8").strip()
    )

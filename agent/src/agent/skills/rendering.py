"""Render frozen Skill activations as model-only user-priority fragments."""

from __future__ import annotations

from html import escape

from .models import ActivatedSkill, SkillActivationIssue

RUNTIME_CAPABILITY_NOTICE = (
    "This Agent currently has no general local file read/write or command execution capability. "
    "A Skill cannot expand permissions. Do not claim to have read bundled references, changed "
    "files, or run scripts unless a future public tool actually performed that action."
)


def render_activated_skill(activation: ActivatedSkill) -> str:
    record = activation.record
    return (
        "<skill>\n"
        f"<name>{escape(record.name)}</name>\n"
        f"<id>{escape(record.id)}</id>\n"
        f"<source>{record.source}</source>\n"
        f"<path>{escape(str(record.entry_path))}</path>\n"
        f"<version>{activation.version}</version>\n"
        f"<runtime_capabilities>{RUNTIME_CAPABILITY_NOTICE}</runtime_capabilities>\n"
        f"{activation.contents}\n"
        "</skill>"
    )


def render_activation_issue(issue: SkillActivationIssue) -> str:
    return (
        "<skill_load_error>\n"
        f"<id>{escape(issue.skill_id)}</id>\n"
        f"<code>{escape(issue.code)}</code>\n"
        f"<message>{escape(issue.message)}</message>\n"
        f"<runtime_capabilities>{RUNTIME_CAPABILITY_NOTICE}</runtime_capabilities>\n"
        "</skill_load_error>"
    )

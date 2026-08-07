"""Agent Skill discovery, catalog, activation, and watcher services."""

from .catalog import render_catalog
from .loader import SkillLoader
from .models import (
    ActivatedSkill,
    RenderedSkillCatalog,
    SkillActivationIssue,
    SkillAlias,
    SkillCatalogSnapshot,
    SkillInvocationKind,
    SkillLoadIssue,
    SkillRecord,
    SkillRoot,
    SkillSelection,
    SkillSource,
    SkillTurnSnapshot,
    SkillWatcherState,
)
from .rendering import render_activated_skill, render_activation_issue
from .selection import decode_skill_selections
from .service import SkillCatalogService, default_shared_skills_dir

__all__ = [
    "ActivatedSkill",
    "RenderedSkillCatalog",
    "SkillActivationIssue",
    "SkillAlias",
    "SkillCatalogService",
    "SkillCatalogSnapshot",
    "SkillInvocationKind",
    "SkillLoadIssue",
    "SkillLoader",
    "SkillRecord",
    "SkillRoot",
    "SkillSelection",
    "SkillSource",
    "SkillTurnSnapshot",
    "SkillWatcherState",
    "decode_skill_selections",
    "default_shared_skills_dir",
    "render_activated_skill",
    "render_activation_issue",
    "render_catalog",
]

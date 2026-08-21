"""Agent Skill discovery, catalog, activation, and watcher services."""

from .catalog import render_catalog
from .first_party_updates import (
    FirstPartySkillConfig,
    FirstPartySkillStore,
    FirstPartySkillUpdateService,
    GitHubReleaseClient,
)
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
from .rendering import (
    render_activated_skill,
    render_activation_issue,
    render_runtime_capability_notice,
)
from .selection import decode_skill_selections, resolve_explicit_skill_selections
from .service import SkillCatalogService, default_shared_skills_dir

__all__ = [
    "ActivatedSkill",
    "FirstPartySkillConfig",
    "FirstPartySkillStore",
    "FirstPartySkillUpdateService",
    "GitHubReleaseClient",
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
    "render_runtime_capability_notice",
    "resolve_explicit_skill_selections",
]

"""Model-callable access to cataloged Agent Skill entry instructions."""

from .tool import (
    SKILL_CATALOG_SNAPSHOT_ARG,
    SKILL_READ_TOOL_NAME,
    SKILL_RUN_CONTEXT_ARG,
    SKILL_RUNTIME_CAPABILITIES_ARG,
    SkillReadTool,
)

__all__ = [
    "SKILL_CATALOG_SNAPSHOT_ARG",
    "SKILL_READ_TOOL_NAME",
    "SKILL_RUNTIME_CAPABILITIES_ARG",
    "SKILL_RUN_CONTEXT_ARG",
    "SkillReadTool",
]

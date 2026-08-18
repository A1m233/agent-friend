"""Read one cataloged ``SKILL.md`` after the model has selected it semantically."""

from __future__ import annotations

from typing import Any, ClassVar

from ....skills.models import SkillCatalogSnapshot, SkillSelection
from ....skills.rendering import (
    render_activated_skill,
    render_activation_issue,
    render_runtime_capability_notice,
)
from ....skills.service import SkillCatalogService
from ...protocol import ToolResult

SKILL_READ_TOOL_NAME = "skill_read"
SKILL_CATALOG_SNAPSHOT_ARG = "__agent_friend_skill_catalog_snapshot"
SKILL_RUNTIME_CAPABILITIES_ARG = "__agent_friend_skill_runtime_capabilities"


class SkillReadTool:
    """Turn-scoped, ID-only reader for full Skill entry instructions."""

    name: ClassVar[str] = SKILL_READ_TOOL_NAME
    result_context_retention: ClassVar[str] = "pinned"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 5.0
    description: ClassVar[str] = (
        "读取当前 Skill catalog 中一个 Skill 的完整 SKILL.md，并在成功后让它对当前用户任务生效。"
        "本工具只用于模型根据当前任务或上下文自行发现的隐式调用；用户通过宿主 Skill 选择器、"
        "显式 Skill 名称或当前列表序号选择的 Skill 已由宿主加载，不要再次读取。只传 catalog "
        "给出的精确 skill_id；不要传名称、序号、filesystem_scope_id 或路径。"
        "allow_implicit_invocation=false 的 Skill 不能通过本工具激活，也不要尝试改变或声明调用类型。"
        "成功结果才代表 Skill 已激活；调用失败、未调用本工具、仅看见 catalog 或调用普通工具"
        "都不算激活，不得声称已使用 Skill。历史中曾加载过也不代表当前任务继续生效，需要时"
        "应再次读取。只选择完成任务所需的最小 Skill 集合。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "当前 skills_catalog 中目标 Skill 的精确稳定 skill_id。只能复制 catalog 的 "
                    "skill_id；不能填 filesystem_scope_id、request_id、名称、序号或路径。"
                ),
            },
            "reason": {
                "type": "string",
                "maxLength": 200,
                "description": "为何当前任务需要该 Skill 的一句简短理由",
            },
        },
        "required": ["skill_id", "reason"],
        "additionalProperties": False,
    }

    def __init__(self, service: SkillCatalogService) -> None:
        self._service = service

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        skill_id = args.get("skill_id")
        reason = args.get("reason")
        snapshot = args.get(SKILL_CATALOG_SNAPSHOT_ARG)
        runtime_capabilities = args.get(SKILL_RUNTIME_CAPABILITIES_ARG)
        if not isinstance(runtime_capabilities, str) or not runtime_capabilities.strip():
            runtime_capabilities = render_runtime_capability_notice((), surface=None)
        if not isinstance(skill_id, str) or not skill_id.strip():
            return self._argument_error(
                "skill_id 必须是当前 catalog 中的非空稳定 Skill ID；"
                "filesystem_scope_id、名称、序号和路径都不能用于此字段。"
            )
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 200:
            return self._argument_error("reason 必须是 1-200 字的简短理由。")
        if not isinstance(snapshot, SkillCatalogSnapshot):
            return ToolResult(
                text="Skill 读取失败：当前用户轮没有可用的 catalog snapshot，未激活。",
                is_error=True,
                meta={"error_kind": "skill_snapshot_unavailable"},
            )

        activation, issue = self._service.activate_from_snapshot(
            snapshot,
            SkillSelection(id=skill_id.strip()),
        )
        if activation is not None:
            return ToolResult(
                text=render_activated_skill(
                    activation,
                    runtime_capabilities=runtime_capabilities,
                ),
                meta={
                    "_activated_skill": activation,
                    "context_retention": "pinned",
                    "skill_activation": {
                        "status": "activated",
                        "skill_id": activation.record.id,
                        "name": activation.record.name,
                        "source": activation.record.source,
                        "version": activation.version,
                        "invocation_kind": "implicit",
                        "reason": reason.strip(),
                    },
                    "filesystem_package_root": str(activation.record.package_root),
                },
            )

        assert issue is not None
        return ToolResult(
            text=render_activation_issue(
                issue,
                runtime_capabilities=runtime_capabilities,
            ),
            is_error=True,
            meta={
                "error_kind": issue.code,
                "context_retention": "pinned",
                "skill_activation": {
                    "status": "error",
                    "skill_id": issue.skill_id,
                    "invocation_kind": "implicit",
                    "reason": reason.strip(),
                    "error_code": issue.code,
                    "error_message": issue.message,
                },
            },
        )

    @staticmethod
    def _argument_error(message: str) -> ToolResult:
        return ToolResult(
            text=f"Skill 读取失败：{message} 未激活。",
            is_error=True,
            meta={"error_kind": "invalid_arguments"},
        )

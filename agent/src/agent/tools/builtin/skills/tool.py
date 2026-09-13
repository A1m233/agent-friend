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
from ....tool_runtime.context import ToolRunContext
from ...protocol import ToolResult

SKILL_READ_TOOL_NAME = "skill_read"
SKILL_CATALOG_SNAPSHOT_ARG = "__agent_friend_skill_catalog_snapshot"
SKILL_RUNTIME_CAPABILITIES_ARG = "__agent_friend_skill_runtime_capabilities"
SKILL_RUN_CONTEXT_ARG = "__agent_friend_skill_run_context"


class SkillReadTool:
    """Turn-scoped, ID-only reader for full Skill entry instructions."""

    name: ClassVar[str] = SKILL_READ_TOOL_NAME
    result_context_retention: ClassVar[str] = "pinned"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 30.0
    description: ClassVar[str] = (
        "读取当前 Skill catalog 中一个 Skill 的完整 SKILL.md，并在成功后让它对当前用户任务生效。"
        "用于按需加载或同轮更新后的再次加载；同版本不重复注入。用户已由宿主显式选择的 "
        "Skill 可重新加载，调用来源由宿主核验。只传 catalog "
        "给出的精确 skill_id；不要传名称、序号、filesystem_scope_id 或路径。"
        "allow_implicit_invocation=false 且本轮没有宿主显式选择的 Skill 不能激活；不要声明调用类型。"
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

        context = args.get(SKILL_RUN_CONTEXT_ARG)
        invocation_kind = (
            context.skill_invocations.get(skill_id.strip(), "implicit")
            if isinstance(context, ToolRunContext)
            else "implicit"
        )
        activation, issue = self._service.activate_from_snapshot(
            snapshot,
            SkillSelection(id=skill_id.strip()),
            invocation_kind=invocation_kind,
        )
        if activation is not None:
            previous = (
                context.skill_activations.get(activation.record.id)
                if isinstance(context, ToolRunContext)
                else None
            )
            unchanged = (
                previous is not None and previous.package_version == activation.package_version
            )
            if unchanged and previous is not None:
                if activation.package is not None:
                    activation.package.close()
                activation = previous
            return ToolResult(
                text=(
                    "Skill 当前版本已加载，无需重复注入。"
                    if unchanged
                    else render_activated_skill(
                        activation,
                        runtime_capabilities=runtime_capabilities,
                    )
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
                        "package_version": activation.package_version,
                        "invocation_kind": invocation_kind,
                        "reason": reason.strip(),
                    },
                    "filesystem_package_root": str(activation.resource_root),
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
                "_skill_blocked": issue.skill_id,
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

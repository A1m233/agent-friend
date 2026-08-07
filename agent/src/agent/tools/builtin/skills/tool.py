"""Read one cataloged ``SKILL.md`` after the model has selected it semantically."""

from __future__ import annotations

from typing import Any, ClassVar, cast

from ....skills.models import SkillCatalogSnapshot, SkillInvocationKind, SkillSelection
from ....skills.rendering import render_activated_skill, render_activation_issue
from ....skills.service import SkillCatalogService
from ...protocol import ToolResult

SKILL_READ_TOOL_NAME = "skill_read"
SKILL_CATALOG_SNAPSHOT_ARG = "__agent_friend_skill_catalog_snapshot"
_INVOCATION_KINDS = frozenset({"named", "referenced", "implicit"})


class SkillReadTool:
    """Turn-scoped, ID-only reader for full Skill entry instructions."""

    name: ClassVar[str] = SKILL_READ_TOOL_NAME
    result_context_retention: ClassVar[str] = "pinned"
    execution_kind: ClassVar[str] = "serial"
    timeout_seconds: ClassVar[float] = 5.0
    description: ClassVar[str] = (
        "读取当前 Skill catalog 中一个 Skill 的完整 SKILL.md，并在成功后让它对当前用户任务生效。"
        "当用户直接说出 Skill 名称或当前列表序号、通过上下文明确指代某个 Skill，或当前任务"
        "明显匹配 description 时，必须先简短说明要使用哪个 Skill 及原因，再调用本工具。"
        "named 只用于直接名称/序号，referenced 用于可唯一消歧的上下文指代，其余任务匹配用"
        " implicit。只传 catalog 给出的精确 id；不要传名称、序号或路径。"
        "成功结果才代表 Skill 已激活；调用失败、未调用本工具、仅看见 catalog 或调用普通工具"
        "都不算激活，不得声称已使用 Skill。历史中曾加载过也不代表当前任务继续生效，需要时"
        "应再次读取。只选择完成任务所需的最小 Skill 集合。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "minLength": 1,
                "description": "当前 skills_catalog 中目标 Skill 的精确稳定 id",
            },
            "invocation_kind": {
                "type": "string",
                "enum": ["named", "referenced", "implicit"],
                "description": "本轮选择来源：直接名称/序号、上下文指代或隐式任务匹配",
            },
            "reason": {
                "type": "string",
                "maxLength": 200,
                "description": "为何当前任务需要该 Skill 的一句简短理由",
            },
        },
        "required": ["id", "invocation_kind", "reason"],
        "additionalProperties": False,
    }

    def __init__(self, service: SkillCatalogService) -> None:
        self._service = service

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        skill_id = args.get("id")
        raw_kind = args.get("invocation_kind")
        reason = args.get("reason")
        snapshot = args.get(SKILL_CATALOG_SNAPSHOT_ARG)
        if not isinstance(skill_id, str) or not skill_id.strip():
            return self._argument_error("id 必须是当前 catalog 中的非空稳定 ID。")
        if raw_kind not in _INVOCATION_KINDS:
            return self._argument_error("invocation_kind 必须是 named、referenced 或 implicit。")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 200:
            return self._argument_error("reason 必须是 1-200 字的简短理由。")
        if not isinstance(snapshot, SkillCatalogSnapshot):
            return ToolResult(
                text="Skill 读取失败：当前用户轮没有可用的 catalog snapshot，未激活。",
                is_error=True,
                meta={"error_kind": "skill_snapshot_unavailable"},
            )

        invocation_kind = cast(SkillInvocationKind, raw_kind)
        activation, issue = self._service.activate_from_snapshot(
            snapshot,
            SkillSelection(id=skill_id.strip()),
            invocation_kind=invocation_kind,
        )
        if activation is not None:
            return ToolResult(
                text=render_activated_skill(activation),
                meta={
                    "context_retention": "pinned",
                    "skill_activation": {
                        "status": "activated",
                        "skill_id": activation.record.id,
                        "name": activation.record.name,
                        "source": activation.record.source,
                        "version": activation.version,
                        "invocation_kind": invocation_kind,
                        "reason": reason.strip(),
                    },
                },
            )

        assert issue is not None
        return ToolResult(
            text=render_activation_issue(issue),
            is_error=True,
            meta={
                "error_kind": issue.code,
                "context_retention": "pinned",
                "skill_activation": {
                    "status": "error",
                    "skill_id": issue.skill_id,
                    "invocation_kind": invocation_kind,
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

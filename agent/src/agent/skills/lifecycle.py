"""Apply verified updates before the next tool or model step in the same turn."""

from __future__ import annotations

import json

from ..tool_runtime.context import ToolRunContext
from ..tools.protocol import ToolResult
from .models import SkillSelection, SkillTurnSnapshot
from .rendering import render_activated_skill, render_activation_issue
from .service import SkillCatalogService
from .update_receipts import SkillUpdateResult


def render_current_bindings(context: ToolRunContext | None, runtime_capabilities: str = "") -> str:
    """Reproject current authority after compaction; history never grants a binding."""
    active = context.skill_activations if context is not None else {}
    blocked = context.blocked_skills if context is not None else {}
    parts = [
        '<skill_bindings source="host">',
        "Only these Skill versions are active now; other versions and old scopes are history. "
        "Activation does not expand tool permissions. Already completed actions must not be repeated.",
    ]
    parts.extend(
        render_activated_skill(active[key], runtime_capabilities=runtime_capabilities or None)
        for key in sorted(active)
    )
    for key in sorted(blocked):
        parts.append(f"Skill {key}: reload required ({blocked[key]}). Do not use the old binding.")
    if not active and not blocked:
        parts.append("No Skill is active on this user turn.")
    parts.append("</skill_bindings>")
    return "\n".join(parts)


def apply_skill_update(
    result: ToolResult,
    *,
    service: SkillCatalogService,
    snapshot: SkillTurnSnapshot,
    context: ToolRunContext,
    runtime_capabilities: str,
) -> ToolResult:
    update = result.meta.get("_skill_update")
    failed = result.meta.get("_skill_update_failed")
    if not isinstance(update, SkillUpdateResult) and not isinstance(failed, str):
        return result
    try:
        payload = json.loads(result.text)
    except (ValueError, TypeError):
        payload = {"command_result": result.text}
    if not isinstance(payload, dict):
        payload = {"command_result": payload}
    meta = dict(result.meta)
    meta["context_retention"] = "pinned"
    if isinstance(failed, str):
        context.block_skill(failed, "skill_update_verification_failed")
        payload["skill_update"] = {
            "skill_id": failed,
            "status": "verification_failed",
            "message": "更新结果未通过核验；不得按新版继续。请核对更新状态后重新激活。",
        }
        meta["error_kind"] = "skill_update_verification_failed"
        return ToolResult(json.dumps(payload, ensure_ascii=False), True, meta)
    assert isinstance(update, SkillUpdateResult)
    payload["skill_update"] = {
        "skill_id": update.skill_id,
        "status": "updated" if update.status == "committed" else update.status,
        "version": update.version,
    }
    if (
        update.skill_id not in context.skill_activations
        and update.skill_id not in context.blocked_skills
    ):
        service.refresh()
        payload["skill_update"]["activation"] = "not_loaded"
        return ToolResult(json.dumps(payload, ensure_ascii=False), result.is_error, meta)
    activation, issue = service.activate_from_snapshot(
        snapshot.catalog,
        SkillSelection(update.skill_id),
        invocation_kind=context.skill_invocations.get(update.skill_id, "implicit"),
    )
    if activation is not None and activation.package_version != update.version:
        if activation.package is not None:
            activation.package.close()
        activation = None
    if activation is None:
        code = issue.code if issue is not None else "skill_update_version_changed"
        context.block_skill(update.skill_id, code)
        payload["skill_update"].update(
            {
                "activation": "failed",
                "error": code,
                "message": "磁盘更新已完成，但新版未加载；先修正原因并重新激活，不能以旧版继续。",
            }
        )
        if issue is not None:
            payload["skill_update"]["details"] = render_activation_issue(issue)
        meta["error_kind"] = code
        return ToolResult(json.dumps(payload, ensure_ascii=False), True, meta)
    previous = context.skill_activations.get(update.skill_id)
    unchanged = previous is not None and previous.package_version == activation.package_version
    activation = context.bind_skill(activation)
    payload["skill_update"]["activation"] = "already_loaded" if unchanged else "reloaded"
    if not unchanged:
        payload["skill_update"]["instructions"] = (
            "此 Skill 已自动切换到以下版本，后续使用新版指引和资源；旧版仅为历史，不重做已完成操作。\n"
            + render_activated_skill(activation, runtime_capabilities=runtime_capabilities)
        )
    meta["_activated_skill"] = activation
    meta["skill_activation"] = {
        "skill_id": update.skill_id,
        "status": "activated",
        "version": activation.version,
        "package_version": activation.package_version,
        "invocation_kind": activation.invocation_kind,
    }
    return ToolResult(json.dumps(payload, ensure_ascii=False), result.is_error, meta)

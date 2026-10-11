"""Narrow output capability for the bundled screenshot entry."""

from __future__ import annotations

from pathlib import Path

from ..tool_runtime.context import ToolRunContext
from .models import CommandApprovalDetails
from .runtime import CommandRuntimeError
from .workspace import reject_linked_path

SCREENSHOT_OUTPUT_ENV = "AGENT_FRIEND_SCREENSHOT_OUTPUT_DIR"


def validate_screenshot_roots(roots: tuple[tuple[Path, bool], ...]) -> None:
    """Reject broad ACL propagation for capture; ordinary commands keep their policy."""
    home = Path.home().resolve()
    for path, writable in roots:
        if writable and (path == Path(path.anchor) or path == home or path in home.parents):
            raise CommandRuntimeError(
                "screenshot_write_scope_too_broad",
                "截图不需要整个 home 或磁盘根可写。移除该 additional_roots 项，"
                "默认保存到宿主提供的会话输出目录；成功后用 output_directory 的 "
                "filesystem_scope_id 和图片文件名调用 view_image。指定外部位置只申请实际目录；"
                "宽根中的单个目标文件通过既有文件操作授权另存，不改用无沙箱执行。",
            )


def screenshot_output_scope(
    details: CommandApprovalDetails, context: ToolRunContext
) -> dict[str, object]:
    raw = details.environment_overrides.get(SCREENSHOT_OUTPUT_ENV)
    if raw is None:
        return {}
    root = Path(raw)
    reject_linked_path(root)
    capability = context.filesystem_authorizations.grant_scope(
        root=root,
        root_kind="directory",
        permissions=frozenset({"read_image"}),
        allow_protected=True,
        source="skill",
        label="本次截图输出",
    )
    return {
        "output_directory": {
            "filesystem_scope_id": capability.scope_id,
            "permissions": ["read_image"],
            "lifetime": "current_user_turn",
            "instruction": "默认截图在此目录。view_image.path 填脚本输出的图片文件名；"
            "显式保存到外部位置或 temp 时，此 scope 不适用，须取得对应位置的 read_image 授权。",
        }
    }

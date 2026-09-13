"""A provider-independent, capability-checked local image tool."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ....attachments import AttachmentError
from ....attachments.models import TOOL_IMAGE_MAX_BYTES
from ....filesystem.models import FilesystemAuthorizationLedger
from ....filesystem.service import FilesystemError, FilesystemService
from ....filesystem.tools import FILESYSTEM_LEDGER_ARG
from ....image_reading import ImageReadingService
from ...protocol import IMAGE_INPUT_SUPPORTED_ARG, ToolResult


class ViewImageTool:
    name: ClassVar[str] = "view_image"
    execution_kind: ClassVar[str] = "serial"
    result_context_retention: ClassVar[str] = "recoverable"
    description: ClassVar[str] = (
        "读取本地静态 PNG/JPEG/WebP，让当前模型直接查看图片。需要观察任务中的图片时使用；"
        "不读取文本、下载 URL、截图或调用其他视觉模型。先通过 request_filesystem_access "
        "取得当前轮 read_image 授权；read_text 不包含看图权限。返回图像及来源/尺寸，"
        "每次一张、最多 8 MiB、1600 万像素、单边 8192。权限错误应取得有效 scope 后再试；"
        "格式或大小错误需更换有效图片；模型不支持时停止，不原样重试。"
        "历史图像被清理时使用 recover_tool_result 的 images 模式恢复，避免重读已变化的文件。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "filesystem_scope_id": {
                "type": "string",
                "minLength": 1,
                "description": "当前轮 request_filesystem_access 成功结果 scopes[].filesystem_scope_id，须含 read_image；不是 request_id、skill_id 或旧轮 scope。",
            },
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "授权目录内的相对图片路径；准确文件 scope 填 .；不得使用绝对路径、URL 或 ..。",
            },
        },
        "required": ["filesystem_scope_id", "path"],
        "additionalProperties": False,
    }

    def __init__(self, filesystem: FilesystemService, images: ImageReadingService) -> None:
        self._filesystem = filesystem
        self._images = images

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        if args.get(IMAGE_INPUT_SUPPORTED_ARG) is not True:
            return ToolResult(
                "image_model_unsupported: 当前模型路由不能接收图像工具结果。请使用支持的模型；不要重试或用文本编码代替图像。",
                is_error=True,
                meta={"error_kind": "image_model_unsupported"},
            )
        try:
            ledger = args.get(FILESYSTEM_LEDGER_ARG)
            if not isinstance(ledger, FilesystemAuthorizationLedger):
                raise FilesystemError("permission_denied", "缺少当前轮文件授权。")
            data, name = self._filesystem.read_image_bytes(
                ledger,
                scope_id=args["filesystem_scope_id"],
                relative_path=args["path"],
                max_bytes=TOOL_IMAGE_MAX_BYTES,
            )
            snapshot = self._images.snapshot(data, display_name=name)
        except (FilesystemError, AttachmentError) as exc:
            return ToolResult(
                f"{exc.code}: {exc} 权限错误请通过 request_filesystem_access 取得当前轮 read_image scope；其他错误请修正图片后再试。",
                is_error=True,
                meta={"error_kind": exc.code},
            )
        return ToolResult(
            text=json.dumps(
                {
                    "image": snapshot.image.display_name,
                    "width": snapshot.width,
                    "height": snapshot.height,
                    "source": "local_file_snapshot",
                },
                ensure_ascii=False,
            ),
            images=(snapshot.image,),
            meta={"evidence_keys": [f"image:{snapshot.image.sha256}"]},
        )

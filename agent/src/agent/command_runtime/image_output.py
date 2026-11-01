"""Opt-in images from buffered commands; stdout cannot grant filesystem access."""

from __future__ import annotations

import json
from typing import Any

from ..attachments import AttachmentRef
from ..attachments.models import TOOL_IMAGE_MAX_BYTES, TOOL_IMAGE_MAX_COUNT, validate_image_refs
from ..filesystem.service import FilesystemService
from ..image_reading import ImageReadingService
from ..tool_runtime.context import ToolRunContext
from ..tools.protocol import IMAGE_INPUT_SUPPORTED_ARG


class CommandImageOutput:
    def __init__(self, filesystem: FilesystemService, images: ImageReadingService) -> None:
        self.filesystem = filesystem
        self.images = images

    def validate(self, args: dict[str, Any], context: ToolRunContext) -> str:
        spec = args["output_images"]
        if not isinstance(spec, dict) or set(spec) != {"filesystem_scope_id"}:
            raise ValueError("output_images 只接受 filesystem_scope_id。")
        scope_id = spec["filesystem_scope_id"]
        if not isinstance(scope_id, str) or not scope_id:
            raise ValueError("output_images 需要当前轮 read_image scope。")
        if args.get("mode", "buffered") != "buffered":
            raise ValueError("output_images 仅支持 buffered 命令；不可用于 streaming。")
        if args.get(IMAGE_INPUT_SUPPORTED_ARG) is not True:
            raise ValueError("image_model_unsupported：当前模型不支持图片，命令未执行。")
        scope = context.filesystem_authorizations.scope(scope_id)
        if scope is None or "read_image" not in scope.permissions:
            raise ValueError("output_images 缺少当前轮 read_image 授权；先申请图片目录 scope。")
        return scope_id

    def collect(
        self, payload: dict[str, object], context: ToolRunContext, scope_id: str
    ) -> tuple[AttachmentRef, ...]:
        if payload.get("dropped_bytes"):
            raise ValueError("图片声明被截断；命令已执行，不要重放操作。")
        output = json.loads(str(payload.get("stdout", "")))
        paths = output.get("image_paths") if isinstance(output, dict) else None
        if (
            not isinstance(paths, list)
            or not 1 <= len(paths) <= TOOL_IMAGE_MAX_COUNT
            or any(not isinstance(path, str) or not path for path in paths)
            or len(set(paths)) != len(paths)
        ):
            raise ValueError("stdout 必须是含 1–8 个不同相对 image_paths 的 JSON 对象。")
        sources = [
            self.filesystem.read_image_bytes(
                context.filesystem_authorizations,
                scope_id=scope_id,
                relative_path=path,
                max_bytes=TOOL_IMAGE_MAX_BYTES,
            )
            for path in paths
        ]
        refs = tuple(self.images.snapshot(data, display_name=name).image for data, name in sources)
        validate_image_refs(refs)
        return refs

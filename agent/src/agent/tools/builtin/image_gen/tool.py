"""Codex-style static image generation and editing on the active connection."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar
from uuid import NAMESPACE_URL, uuid5

import httpx
from llm_providers.image_output import LLMImageResult
from llm_providers.images_api import ImagesAPIError, request_image
from llm_providers.spec import ProviderSpec

from ....attachments import AttachmentError
from ....attachments.models import TOOL_IMAGE_MAX_BYTES
from ....filesystem.models import FilesystemAuthorizationLedger
from ....filesystem.service import FilesystemError, FilesystemService
from ....filesystem.tools import (
    FILESYSTEM_LEDGER_ARG,
    FILESYSTEM_SESSION_ID_ARG,
    FILESYSTEM_TOOL_CALL_ID_ARG,
)
from ....generated_images import save_generated_image
from ....image_reading.service import validate_image
from ....session_files.service import SessionFileError, SessionFileService
from ....sessions import Event
from ....tool_runtime.control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl
from ...protocol import ToolResult

IMAGE_SPEC_ARG = "__agent_friend_image_spec"
IMAGE_DUPLICATE_ARG = "__agent_friend_image_duplicate"


def image_message_id(session_id: str, call_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"agent-friend:imagegen:{session_id}:{call_id}"))


def image_request_already_handled(
    events: Iterable[Event], call_id: str, args: dict[str, Any]
) -> bool:
    for event in events:
        payload = event.payload
        if event.type == "tool_call_request" and payload.get("tool_name") == "imagegen":
            if payload.get("event_tool_call_id") == call_id:
                return False
            if payload.get("args") == args:
                return True
        if (
            event.type == "assistant_message"
            and payload.get("generated_image") is not None
            and payload.get("attachments")
            and not payload.get("imagegen_call_id")
        ):
            return True
    return False


class ImagegenTool:
    name: ClassVar[str] = "imagegen"
    execution_kind: ClassVar[str] = "serial"
    supports_invocation_control = True
    timeout_seconds = 180.0
    description: ClassVar[str] = (
        "Generate or edit an actual image in the current conversation. 用户要求画图、生图、编辑图片时调用本工具。"
        "新图只需 prompt；编辑时用 reference_images 指定会话 file_ref 或授权文件，或用 "
        "num_last_images_to_include 取最近的会话图片，两种方式互斥，最多五张。"
        "编辑未看过的本地文件应先 view_image。编辑应保留未指定修改的内容和原有透明背景；"
        "需要保留或生成透明背景时传 transparent_background。"
        "明确要求编辑但原图不可定位时请用户指定或补图，不能改为凭空生成。"
        "成功后图片自动显示在本轮；失败后说明原因，同一请求不要重试或用代码替代。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "required": ["prompt"],
        "additionalProperties": False,
        "properties": {
            "prompt": {"type": "string", "minLength": 1, "maxLength": 16000},
            "transparent_background": {"type": "boolean"},
            "num_last_images_to_include": {"type": "integer", "minimum": 1, "maximum": 5},
            "reference_images": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {
                                "file_ref": {"type": "string", "pattern": "^file-[0-9a-f]{64}$"}
                            },
                            "required": ["file_ref"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "filesystem_scope_id": {"type": "string", "minLength": 1},
                                "path": {"type": "string", "minLength": 1},
                            },
                            "required": ["filesystem_scope_id", "path"],
                            "additionalProperties": False,
                        },
                    ]
                },
            },
        },
        "not": {"required": ["reference_images", "num_last_images_to_include"]},
    }

    def __init__(self, files: SessionFileService, filesystem: FilesystemService) -> None:
        self.files = files
        self.filesystem = filesystem

    def _inputs(self, args: dict[str, Any], session: str) -> list[tuple[str, bytes, str]]:
        refs = args.get("reference_images", [])
        count = args.get("num_last_images_to_include")
        if count is not None:
            recent = [f.file_ref for f in self.files.files(session) if f.attachment.kind == "image"]
            if len(recent) < count:
                raise SessionFileError("session_reference_images_missing")
            refs = [{"file_ref": ref} for ref in recent[-count:]]
        inputs: list[tuple[str, bytes, str]] = []
        for ref in refs:
            if "file_ref" in ref:
                data, _ = self.files.read_bytes(
                    session, ref["file_ref"], max_bytes=TOOL_IMAGE_MAX_BYTES
                )
            else:
                ledger = args.get(FILESYSTEM_LEDGER_ARG)
                if not isinstance(ledger, FilesystemAuthorizationLedger):
                    raise FilesystemError("permission_denied", "请先取得当前轮 read_image 授权。")
                data, _ = self.filesystem.read_image_bytes(
                    ledger,
                    scope_id=ref["filesystem_scope_id"],
                    relative_path=ref["path"],
                    max_bytes=TOOL_IMAGE_MAX_BYTES,
                )
            validate_image(data)
            ext = (
                "png"
                if data.startswith(b"\x89PNG")
                else "jpeg"
                if data.startswith(b"\xff\xd8")
                else "webp"
            )
            inputs.append((f"reference-{len(inputs) + 1}.{ext}", data, f"image/{ext}"))
        return inputs

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        session = args.get(FILESYSTEM_SESSION_ID_ARG)
        call = args.get(FILESYSTEM_TOOL_CALL_ID_ARG)
        spec = args.get(IMAGE_SPEC_ARG)
        control = args.get(TOOL_INVOCATION_CONTROL_ARG)
        if not isinstance(spec, ProviderSpec) or spec.images is None or not session or not call:
            return ToolResult(
                "当前连接没有可用的独立图片服务。",
                is_error=True,
                meta={"error_kind": "image_route_unavailable"},
            )
        if args.get(IMAGE_DUPLICATE_ARG):
            return ToolResult(
                "本轮已处理相同图片请求，请使用已有结果，不要重试。",
                is_error=True,
                meta={"error_kind": "image_duplicate"},
            )
        prompt = args["prompt"].strip()
        if not prompt:
            return ToolResult(
                "图片描述不能为空。", is_error=True, meta={"error_kind": "image_prompt_missing"}
            )
        try:
            inputs = self._inputs(args, session)
            with httpx.Client(timeout=self.timeout_seconds - 5) as client:
                token = (
                    control.add_cancel_callback(lambda _: client.close())
                    if isinstance(control, ToolInvocationControl)
                    else None
                )
                try:
                    if isinstance(control, ToolInvocationControl):
                        control.check_cancelled()
                    data = request_image(
                        client,
                        spec.images,
                        spec.api_key,
                        prompt,
                        inputs,
                        args.get("transparent_background", False),
                    )
                    if isinstance(control, ToolInvocationControl):
                        control.check_cancelled()
                finally:
                    if token is not None and isinstance(control, ToolInvocationControl):
                        control.remove_cancel_callback(token)
            mid = image_message_id(session, call)
            text, images, error = save_generated_image(
                LLMImageResult(mid, 0, data), self.files.assets, mid
            )
            if error:
                return ToolResult(text, is_error=True, meta={"error_kind": error})
            return ToolResult(text, generated_image=images[0])
        except (ImagesAPIError, FilesystemError, AttachmentError) as exc:
            return ToolResult(
                f"{exc} 请说明原因，不要自动重试。", is_error=True, meta={"error_kind": exc.code}
            )
        except SessionFileError as exc:
            return ToolResult(
                f"{exc}：请确认当前会话的原图或重新上传。",
                is_error=True,
                meta={"error_kind": str(exc)},
            )
        except OSError:
            return ToolResult(
                "图片文件读取或保存失败。",
                is_error=True,
                meta={"error_kind": "image_storage_failed"},
            )

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from agent.attachments import AttachmentStore
from agent.command_runtime.image_output import CommandImageOutput
from agent.command_runtime.tools import ExecCommandTool
from agent.context.tool_projection import bound_tool_images
from agent.filesystem import FilesystemService, ProtectedPathPolicy
from agent.filesystem.service import FilesystemError
from agent.image_reading import ImageReadingService
from agent.tool_runtime import ToolRunContext
from agent.tools.protocol import IMAGE_INPUT_SUPPORTED_ARG
from jsonschema import Draft202012Validator
from PIL import Image

from agent import Message


def setup(tmp_path: Path) -> tuple[CommandImageOutput, ToolRunContext, str]:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    (tmp_path / "frame.png").write_bytes(buffer.getvalue())
    context = ToolRunContext.create("")
    scope = context.filesystem_authorizations.grant_scope(
        root=tmp_path, root_kind="directory", permissions=frozenset({"read_image"})
    )
    policy = ProtectedPathPolicy(product_roots=(), personal_skill_roots=(), builtin_skill_roots=())
    output = CommandImageOutput(
        FilesystemService(policy), ImageReadingService(AttachmentStore(tmp_path / "assets"))
    )
    return output, context, scope.scope_id


def test_command_images_roundtrip_and_permission_boundary(tmp_path: Path) -> None:
    output, context, scope = setup(tmp_path)
    args = {"output_images": {"filesystem_scope_id": scope}, IMAGE_INPUT_SUPPORTED_ARG: True}
    assert output.validate(args, context) == scope
    refs = output.collect({"stdout": json.dumps({"image_paths": ["frame.png"]})}, context, scope)
    assert refs[0].display_name == "frame.png"
    with pytest.raises(ValueError, match="read_image"):
        output.validate({**args, "output_images": {"filesystem_scope_id": "stale"}}, context)
    with pytest.raises(ValueError, match="不支持图片"):
        output.validate({**args, IMAGE_INPUT_SUPPORTED_ARG: False}, context)
    with pytest.raises(ValueError, match="streaming"):
        output.validate({**args, "mode": "streaming"}, context)
    with pytest.raises(FilesystemError):
        output.collect({"stdout": '{"image_paths":["../outside.png"]}'}, context, scope)
    with pytest.raises(ValueError, match="截断"):
        output.collect(
            {"stdout": '{"image_paths":["frame.png"]}', "dropped_bytes": 1}, context, scope
        )


def test_skill_bound_image_command_passes_full_schema() -> None:
    args = {
        "cmd": "python scripts/game.py dialogue --observation-id " + "a" * 32,
        "skill": {"skill_id": "skill_test", "entry": "scripts/game.py"},
        "output_images": {"filesystem_scope_id": "scope_test"},
        "access": {"filesystem": "read_write", "network": "disabled"},
    }
    validator = Draft202012Validator(ExecCommandTool.parameters_schema)
    validator.validate(args)
    validator.validate({**args, "mode": "buffered"})
    assert not validator.is_valid({**args, "mode": "streaming"})
    assert not validator.is_valid({**args, "argv": ["python", "scripts/game.py"]})


def test_segmented_images_preserve_prefix_and_latest_batch(tmp_path: Path) -> None:
    output, context, scope = setup(tmp_path)
    image = output.collect({"stdout": '{"image_paths":["frame.png"]}'}, context, scope)[0]
    history = [
        Message(role="tool", content=f"observation {i}", images=(image,), uuid=str(i))
        for i in range(18)
    ]
    for count in range(2, 11):
        previous, _ = bound_tool_images(history[: count - 1])
        current, _ = bound_tool_images(history[:count])
        assert current[:-1] == previous
    eleven, omitted = bound_tool_images(history[:11])
    assert omitted == 7 and sum(len(m.images) for m in eleven) == 4
    assert "observation 0" in eleven[0].content  # image eviction preserves text
    twelve, _ = bound_tool_images(history[:12])
    assert twelve[:-1] == eleven
    batch = Message(role="tool", content="dialogue pages", images=(image,) * 8)
    projected, _ = bound_tool_images([*history[:10], batch])
    assert projected[-1].images == batch.images
    assert sum(len(m.images) for m in projected) <= 10

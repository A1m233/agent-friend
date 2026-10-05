"""Bounded DeepSeek tool-image transport smoke; no native user attachment uploads."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from agent_bridge.runtime_config import resolve_bridge_runtime_config
from PIL import Image, ImageDraw, ImageFont

from agent import AttachmentRef
from llm_providers import (
    LLMClient,
    MultimodalInputAdapter,
)
from runtime_config import credential_source_for_mode

MODEL_ID = "deepseek/deepseek-flash"
VISIBLE_CODE = "AF-094"


def _create_fixtures(root: Path) -> dict[str, Path]:
    image = Image.new("RGB", (640, 320), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 610, 290), outline="black", width=8)
    draw.rectangle((70, 190, 210, 260), fill="#e84a5f")
    draw.ellipse((250, 180, 390, 270), fill="#2a9d8f")
    draw.polygon(((470, 265), (540, 175), (610, 265)), fill="#457b9d")
    font = ImageFont.load_default(size=72)
    draw.text((165, 65), VISIBLE_CODE, fill="black", font=font)

    paths = {
        "png": root / "af-094.png",
        "jpeg": root / "af-094.jpg",
        "webp": root / "af-094.webp",
    }
    image.save(paths["png"], format="PNG")
    image.save(paths["jpeg"], format="JPEG", quality=92)
    image.save(paths["webp"], format="WEBP", quality=92)
    return paths


def _attachment(path: Path, media_type: str) -> AttachmentRef:
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    return AttachmentRef(
        asset_id=digest,
        display_name=path.name,
        kind="image",
        media_type=media_type,
        size_bytes=len(content),
        sha256=digest,
    )


def _assert_visible_code(text: str, *, scenario: str) -> None:
    normalized = text.strip().strip("`").strip().upper()
    if VISIBLE_CODE not in normalized:
        raise AssertionError(f"{scenario}: model did not return the expected visible code")


def run() -> dict[str, Any]:
    from agent import Message
    from llm_providers import ModelCallControl, ModelRequest

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = runtime_config.llm_client_factory(MODEL_ID)
    if not isinstance(client, LLMClient):
        raise AssertionError("DeepSeek vision route did not resolve to LLMClient")
    observations = []
    with tempfile.TemporaryDirectory(prefix="agent-friend-vision-smoke-") as raw_temp:
        fixtures = _create_fixtures(Path(raw_temp))
        for name, media in (("png", "image/png"), ("jpeg", "image/jpeg"), ("webp", "image/webp")):
            ref = _attachment(fixtures[name], media)
            adapter = MultimodalInputAdapter(
                open_asset=lambda _, path=fixtures[name]: path.open("rb")
            )
            messages = [
                {
                    "role": "user",
                    "content": "Read the image returned by view_image. Reply only with the code.",
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "fixture-image",
                            "type": "function",
                            "function": {"name": "view_image", "arguments": "{}"},
                        }
                    ],
                },
                adapter.render_message(
                    Message(
                        role="tool",
                        content="synthetic fixture image",
                        images=(ref,),
                        meta={"tool_call_id": "fixture-image"},
                    ),
                    client,
                ),
            ]
            response = client.generate(
                ModelRequest(
                    messages=messages,
                    settings={"max_tokens": 512},
                    control=ModelCallControl(allow_retries=False),
                )
            )
            _assert_visible_code(response.text, scenario=name)
            observations.append({"format": name, "passed": True})
    return {
        "model": MODEL_ID,
        "real_model_calls": 3,
        "remote_file_uploads_expected": 0,
        "scope": "synthetic tool image transport, not agent tool selection",
        "observations": observations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="required acknowledgement that this smoke sends three real DeepSeek requests",
    )
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("real LLM calls require explicit --allow-llm")
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

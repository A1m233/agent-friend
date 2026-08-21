"""Real DeepSeek vision + Files API smoke for requirement 094."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_bridge.runtime_config import resolve_bridge_runtime_config
from PIL import Image, ImageDraw, ImageFont

from agent import AttachmentRef
from llm_providers import (
    LLMClient,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    MultimodalInputAdapter,
)
from runtime_config import credential_source_for_mode

MODEL_ID = "deepseek/deepseek-v4-flash-vision-exp"
VISIBLE_CODE = "AF-094"


@dataclass(frozen=True)
class _Message:
    content: str
    attachment: AttachmentRef
    role: str = "user"

    @property
    def attachments(self) -> list[AttachmentRef]:
        return [self.attachment]

    @property
    def meta(self) -> dict[str, bool]:
        return {"current_input": True}

    def to_openai(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class _CallObservation:
    scenario: str
    passed: bool
    text_chars: int
    stop_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    transport: str | None


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


def _collect_call(
    client: LLMClient,
    messages: list[dict[str, Any]],
    *,
    scenario: str,
    tools: list[dict[str, Any]] | None = None,
    **settings: Any,
) -> tuple[_CallObservation, list[LLMToolCallDelta], str]:
    text_parts: list[str] = []
    tool_deltas: list[LLMToolCallDelta] = []
    done: LLMTurnDone | None = None
    for event in client.stream(messages, tools=tools, max_tokens=128, **settings):
        if isinstance(event, LLMTextDelta):
            text_parts.append(event.text)
        elif isinstance(event, LLMToolCallDelta):
            tool_deltas.append(event)
        elif isinstance(event, LLMTurnDone):
            done = event
    if done is None:
        raise AssertionError(f"{scenario}: stream ended without LLMTurnDone")
    text = "".join(text_parts)
    usage = done.usage
    diagnostics = done.diagnostics
    observation = _CallObservation(
        scenario=scenario,
        passed=True,
        text_chars=len(text),
        stop_reason=done.stop_reason,
        prompt_tokens=usage.prompt_tokens if usage is not None else None,
        completion_tokens=usage.completion_tokens if usage is not None else None,
        transport=diagnostics.transport if diagnostics is not None else None,
    )
    return observation, tool_deltas, text


def _assert_visible_code(text: str, *, scenario: str) -> None:
    normalized = text.strip().strip("`").strip().upper()
    if VISIBLE_CODE not in normalized:
        raise AssertionError(f"{scenario}: model did not return the expected visible code")


def _tool_call_from_deltas(deltas: list[LLMToolCallDelta]) -> dict[str, Any]:
    calls: dict[int, dict[str, str]] = {}
    for delta in deltas:
        call = calls.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
        if delta.tool_call_id:
            call["id"] = delta.tool_call_id
        if delta.tool_name:
            call["name"] = delta.tool_name
        call["arguments"] += delta.args_json_delta
    if len(calls) != 1:
        raise AssertionError("tool_call: provider did not return exactly one tool call")
    call = next(iter(calls.values()))
    if not call["id"] or call["name"] != "report_visual_code":
        raise AssertionError("tool_call: provider returned an unexpected tool identity")
    try:
        arguments = json.loads(call["arguments"])
    except json.JSONDecodeError as exc:
        raise AssertionError("tool_call: provider returned invalid JSON arguments") from exc
    code = arguments.get("code") if isinstance(arguments, dict) else None
    if not isinstance(code, str) or code.strip().upper() != VISIBLE_CODE:
        raise AssertionError("tool_call: provider did not pass the visible code to the tool")
    return {
        "id": call["id"],
        "type": "function",
        "function": {"name": call["name"], "arguments": call["arguments"]},
    }


def run() -> dict[str, Any]:
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = runtime_config.llm_client_factory(MODEL_ID)
    if not isinstance(client, LLMClient):
        raise AssertionError("DeepSeek vision route did not resolve to LLMClient")

    with tempfile.TemporaryDirectory(prefix="agent-friend-deepseek-vision-smoke-") as raw_temp:
        root = Path(raw_temp)
        fixtures = _create_fixtures(root)
        refs = {
            "png": _attachment(fixtures["png"], "image/png"),
            "jpeg": _attachment(fixtures["jpeg"], "image/jpeg"),
            "webp": _attachment(fixtures["webp"], "image/webp"),
        }
        asset_paths = {ref.asset_id: fixtures[name] for name, ref in refs.items()}
        cache_dir = root / "provider-cache"
        adapter = MultimodalInputAdapter(
            open_asset=lambda asset_id: asset_paths[asset_id].open("rb"),
            cache_dir=cache_dir,
        )

        rendered: dict[str, dict[str, Any]] = {}
        observations: list[_CallObservation] = []
        prompt = "Read the code shown in the image. Reply with only the code."
        for image_format in ("png", "jpeg", "webp"):
            rendered[image_format] = adapter.render_message(
                _Message(prompt, refs[image_format]), client
            )
            observation, tool_deltas, text = _collect_call(
                client,
                [rendered[image_format]],
                scenario=f"vision_{image_format}",
            )
            if tool_deltas:
                raise AssertionError(f"vision_{image_format}: unexpected tool call")
            _assert_visible_code(text, scenario=f"vision_{image_format}")
            observations.append(observation)

        cache_before = {path.name: path.stat().st_mtime_ns for path in cache_dir.glob("*.json")}
        only_image = adapter.render_message(_Message("", refs["png"]), client)
        cache_after = {path.name: path.stat().st_mtime_ns for path in cache_dir.glob("*.json")}
        if cache_before != cache_after or len(cache_after) != 3:
            raise AssertionError("files_reuse: cached PNG was unexpectedly re-uploaded")
        observation, tool_deltas, text = _collect_call(
            client,
            [
                {
                    "role": "system",
                    "content": "Read the code in the attached image and reply with only the code.",
                },
                only_image,
            ],
            scenario="vision_png_reuse_only_image",
        )
        if tool_deltas:
            raise AssertionError("vision_png_reuse_only_image: unexpected tool call")
        _assert_visible_code(text, scenario="vision_png_reuse_only_image")
        observations.append(observation)

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "report_visual_code",
                    "description": "Report the exact code visible in the attached image.",
                    "parameters": {
                        "type": "object",
                        "properties": {"code": {"type": "string"}},
                        "required": ["code"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
        tool_observation, tool_deltas, tool_text = _collect_call(
            client,
            [rendered["png"]],
            scenario="tool_call",
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "report_visual_code"}},
        )
        if tool_text.strip() or tool_observation.stop_reason != "tool_use":
            raise AssertionError("tool_call: provider did not stop for the requested tool")
        tool_call = _tool_call_from_deltas(tool_deltas)
        observations.append(tool_observation)

        followup_observation, followup_deltas, followup_text = _collect_call(
            client,
            [
                rendered["png"],
                {"role": "assistant", "content": None, "tool_calls": [tool_call]},
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(
                        {"accepted": True, "instruction": "Reply only DONE."},
                        separators=(",", ":"),
                    ),
                },
            ],
            scenario="tool_followup",
        )
        if followup_deltas or followup_text.strip().upper() != "DONE":
            raise AssertionError("tool_followup: provider did not complete the tool loop")
        observations.append(followup_observation)

        return {
            "model": MODEL_ID,
            "real_model_calls": len(observations),
            "remote_file_uploads_expected": 3,
            "remote_file_retrievals_expected": 1,
            "cache_entries": len(cache_after),
            "cache_reuse_without_reupload": cache_before == cache_after,
            "visible_code": VISIBLE_CODE,
            "observations": [asdict(item) for item in observations],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="required acknowledgement that this smoke sends six real DeepSeek requests",
    )
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("real LLM calls require explicit --allow-llm")
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

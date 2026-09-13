from __future__ import annotations

import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from llm_providers.catalog import DEEPSEEK_VISION_CAPABILITIES
from llm_providers.multimodal.tool_images import supports_tool_images
from llm_providers.responses.request import convert_messages
from llm_providers.spec import ResponsesRoute

from llm_providers import LLMClient, MultimodalInputAdapter, ProviderSpec


def client(profile: str = "deepseek", *, supported: bool = True, chat: bool = False) -> LLMClient:
    return LLMClient(
        ProviderSpec(
            model="openai/test",
            api_key="test",
            context_window=128000,
            attachment_capabilities=replace(
                DEEPSEEK_VISION_CAPABILITIES, multimodal_tool_results=supported
            ),
            responses=None if chat else ResponsesRoute("test", "https://example.test", profile),  # type: ignore[arg-type]
        )
    )


def message(data: bytes = b"validated image snapshot") -> SimpleNamespace:
    digest = hashlib.sha256(data).hexdigest()
    return SimpleNamespace(
        role="tool",
        content="snapshot",
        meta={"tool_call_id": "call-img"},
        images=[
            SimpleNamespace(
                kind="image",
                media_type="image/png",
                asset_id=digest,
                sha256=digest,
                size_bytes=len(data),
                display_name="local.png",
            ),
        ],
    )


@pytest.mark.parametrize("profile", ["openai", "deepseek"])
def test_image_is_native_tool_output_not_user_or_string(tmp_path: Path, profile: str) -> None:
    data = b"validated image snapshot"
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(data), cache_dir=tmp_path)
    rendered = adapter.render_message(message(data), client(profile))
    wire = convert_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-img",
                        "type": "function",
                        "function": {"name": "view_image", "arguments": "{}"},
                    }
                ],
            },
            rendered,
        ]
    )
    assert wire[-1]["type"] == "function_call_output"
    assert wire[-1]["call_id"] == "call-img"
    assert wire[-1]["output"][1]["type"] == "input_image"
    assert wire[-1]["output"][1]["image_url"].startswith("data:image/png;base64,")
    assert not any(item.get("role") == "user" for item in wire)


@pytest.mark.parametrize("kwargs", [{"chat": True}, {"supported": False}, {"profile": "kimi"}])
def test_route_gate_does_not_read_assets_when_unsupported(tmp_path: Path, kwargs: dict) -> None:  # type: ignore[type-arg]
    def forbidden(_id: str) -> BytesIO:
        raise AssertionError("must not read or upload")

    llm = client(**kwargs)
    assert not supports_tool_images(llm)
    rendered = MultimodalInputAdapter(open_asset=forbidden, cache_dir=tmp_path).render_message(
        message(), llm
    )
    assert rendered["role"] == "tool"
    assert "未提供" in rendered["content"]


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_corrupt_asset_becomes_explicit_marker(tmp_path: Path, missing: bool) -> None:
    def source(_id: str) -> BytesIO:
        if missing:
            raise FileNotFoundError("private path")
        return BytesIO(b"wrong bytes")

    rendered = MultimodalInputAdapter(open_asset=source, cache_dir=tmp_path).render_message(
        message(), client()
    )
    assert all(block["type"] == "text" for block in rendered["content"])
    assert "未提供" in rendered["content"][-1]["text"]
    assert "private path" not in str(rendered)

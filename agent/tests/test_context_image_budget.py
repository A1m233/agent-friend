from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from agent.attachments import AttachmentRef
from agent.context import SummarizingContextManager
from agent.context.usage_anchor import RequestMeasurement, UsageAnchor
from agent.messages import Message
from llm_providers.catalog import KIMI_MULTIMODAL_CAPABILITIES
from llm_providers.spec import ResponsesRoute

from llm_providers import LLMClient, LLMUsage, MultimodalInputAdapter, ProviderSpec

from .test_context_summarizing import _make_conversation


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_local_image_candidate_reuses_usage_and_counts_only_new_images(
    tmp_path: Path, restart: bool, native: bool
) -> None:
    data = b"local immutable image"
    sha = hashlib.sha256(data).hexdigest()
    ref = AttachmentRef(sha, "game.png", "image", "image/png", len(data), sha)
    adapter = MultimodalInputAdapter(open_asset=lambda _: BytesIO(data))
    llm = LLMClient(
        ProviderSpec(
            model="openai/kimi-k3",
            api_key="fixture",
            context_window=800000,
            attachment_capabilities=KIMI_MULTIMODAL_CAPABILITIES,
            responses=ResponsesRoute(model="fixture", api_base="https://unused.invalid")
            if native
            else None,
        )
    )
    conv, _, _ = _make_conversation(
        tmp_path, fake_llm=llm, context_manager=SummarizingContextManager()
    )
    conv._message_budget_renderer = adapter.render_budget_message
    conv._message_renderer = lambda *_: pytest.fail("candidate invoked general renderer")
    messages = [
        Message(role="tool", content="page", meta={"tool_call_id": str(i)}, images=(ref,) * 5)
        for i in range(2)
    ]
    rendered = conv._project_image_transport([adapter.render_message(m, llm) for m in messages])
    anchor = UsageAnchor.from_usage(
        RequestMeasurement.capture(
            rendered,
            conv._usage_scope(None),
            compatibility_scope=conv._usage_scope(None, compatibility_only=True),
        ),
        LLMUsage(prompt_tokens=146393, input_token_accounting="inclusive_total"),
    )
    assert anchor is not None
    conv._usage_anchor = UsageAnchor.restore(anchor.payload()) if restart else anchor
    estimate = conv._estimate_context_input(messages, None)
    assert estimate.tokens == 146393
    assert estimate.retained_components == 0
    candidate = [
        *messages,
        Message(role="tool", content="new", meta={"tool_call_id": "new"}, images=(ref,)),
    ]
    grown = conv._estimate_context_input(candidate, None)
    assert 1536 <= grown.tokens - estimate.tokens < 17000
    if native:
        assert grown.tokens - estimate.tokens >= 16384
    assert (
        grown.tokens
        == conv._estimate_rendered_input(
            [adapter.render_message(m, llm) for m in candidate],
            None,
            calibration_key=conv._input_calibration_key(candidate),
        ).tokens
    )


def test_budget_adapter_matches_missing_asset_and_unsupported_route() -> None:
    sha = "a" * 64
    message = Message(
        role="tool",
        content="look",
        meta={"tool_call_id": "call"},
        images=(AttachmentRef(sha, "a.png", "image", "image/png", 4, sha),),
    )

    def missing(_: str) -> BytesIO:
        raise FileNotFoundError

    adapter = MultimodalInputAdapter(open_asset=missing)
    for rejected in (False, True):
        client = LLMClient(
            ProviderSpec(
                model="openai/kimi-k3",
                api_key="fixture",
                context_window=800000,
                attachment_capabilities=KIMI_MULTIMODAL_CAPABILITIES,
            )
        )
        client.tool_images_rejected = rejected
        assert adapter.render_budget_message(message, client) == adapter.render_message(
            message, client
        )

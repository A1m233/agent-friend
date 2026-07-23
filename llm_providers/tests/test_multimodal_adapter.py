from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_providers import LLMBadRequestError, LLMClient, MultimodalInputAdapter, ProviderSpec


class _Message:
    role = "user"

    def __init__(self, attachment: object, *, current: bool = False) -> None:
        self.content = "look"
        self.attachments = [attachment]
        self.meta = {"current_input": current}

    def to_openai(self):  # type: ignore[no-untyped-def]
        return {"role": self.role, "content": self.content}


class _Files:
    def __init__(self) -> None:
        self.uploads = 0
        self.purposes: list[str] = []
        self.retrievals = 0
        self.retrieve_error = False

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.uploads += 1
        self.purposes.append(kwargs["purpose"])
        return SimpleNamespace(id="remote-file")

    def content(self, _file_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(text="extracted document")

    def retrieve(self, file_id: str) -> SimpleNamespace:
        self.retrievals += 1
        if self.retrieve_error:
            raise RuntimeError("remote file expired")
        return SimpleNamespace(id=file_id)


def _attachment(*, kind: str, media_type: str):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        asset_id="a" * 64,
        display_name="sample.png" if kind == "image" else "sample.pdf",
        kind=kind,
        media_type=media_type,
    )


def _client(model: str, *, context_window: int | None = None) -> LLMClient:
    return LLMClient(
        ProviderSpec(
            model=model,
            api_key="secret",
            api_base="https://example.test",
            context_window=context_window,
        )
    )


def test_text_only_route_projects_historical_attachment_to_marker(tmp_path: Path) -> None:
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"x"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="image", media_type="image/png"))

    rendered = adapter.render_message(message, _client("deepseek/deepseek-v4-flash"))

    assert "当前模型无法直接读取" in rendered["content"]
    assert "sample.png" in rendered["content"]


def test_text_only_route_rejects_current_attachment(tmp_path: Path) -> None:
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"x"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="image", media_type="image/png"), current=True)

    with pytest.raises(LLMBadRequestError, match="attachment_model_unsupported"):
        adapter.render_message(message, _client("deepseek/deepseek-v4-flash"))


def test_kimi_image_upload_is_cached_by_asset_and_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"png"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="image", media_type="image/png"), current=True)
    client = _client("openai/kimi-k2.6")

    first = adapter.render_message(message, client)
    second = adapter.render_message(message, client)

    assert first == second
    assert first["content"][1]["image_url"]["url"] == "ms://remote-file"
    assert files.uploads == 1
    assert files.retrievals == 1
    assert files.purposes == ["image"]


def test_kimi_expired_cached_image_is_reuploaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"png"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="image", media_type="image/png"), current=True)
    client = _client("openai/kimi-k2.6")

    adapter.render_message(message, client)
    files.retrieve_error = True
    adapter.render_message(message, client)

    assert files.retrievals == 1
    assert files.uploads == 2


def test_kimi_document_extract_is_wrapped_as_untrusted_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"pdf"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="document", media_type="application/pdf"), current=True)

    rendered = adapter.render_message(message, _client("openai/kimi-k2.6"))

    block = rendered["content"][1]["text"]
    assert '"trust": "untrusted_data"' in block
    assert "extracted document" in block
    assert files.purposes == ["file-extract"]


def test_kimi_rejects_extracted_document_that_cannot_fit_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    files.content = lambda _file_id: SimpleNamespace(text="x" * 10_000)  # type: ignore[method-assign]
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"pdf"), cache_dir=tmp_path)
    client = _client("openai/kimi-k2.6", context_window=10_000)

    with pytest.raises(LLMBadRequestError, match="attachment_context_too_large"):
        adapter.render_message(
            _Message(_attachment(kind="document", media_type="application/pdf"), current=True),
            client,
        )

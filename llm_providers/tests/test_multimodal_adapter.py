from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_providers import (
    LLMBadRequestError,
    LLMClient,
    LLMProviderError,
    MultimodalInputAdapter,
    ProviderSpec,
)


class _Message:
    role = "user"

    def __init__(self, attachment: object, *, current: bool = False, content: str = "look") -> None:
        self.content = content
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
        self.expires_after: list[dict[str, object]] = []
        self.expires_at = 2_000_000
        self.create_error = False

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.uploads += 1
        if self.create_error:
            raise RuntimeError("secret upstream body")
        self.purposes.append(kwargs["purpose"])
        if "expires_after" in kwargs:
            self.expires_after.append(kwargs["expires_after"])
        return SimpleNamespace(id="remote-file", expires_at=self.expires_at)

    def content(self, _file_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(text="extracted document")

    def retrieve(self, file_id: str) -> SimpleNamespace:
        self.retrievals += 1
        if self.retrieve_error:
            raise RuntimeError("remote file expired")
        return SimpleNamespace(id=file_id)


def _attachment(
    *,
    kind: str,
    media_type: str,
    asset_id: str = "a" * 64,
    size_bytes: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        asset_id=asset_id,
        display_name="sample.png" if kind == "image" else "sample.pdf",
        kind=kind,
        media_type=media_type,
        size_bytes=size_bytes,
    )


def _client(model: str, *, context_window: int = 256 * 1024) -> LLMClient:
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


@pytest.mark.parametrize(
    "model",
    (
        "openai/kimi-k2.6",
        "openai/kimi-k2.7-code",
        "openai/kimi-k2.7-code-highspeed",
    ),
)
def test_kimi_image_upload_is_cached_by_asset_and_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    files = _Files()
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"png"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="image", media_type="image/png"), current=True)
    client = _client(model)

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


@pytest.mark.parametrize(
    "model",
    (
        "openai/kimi-k2.6",
        "openai/kimi-k2.7-code",
        "openai/kimi-k2.7-code-highspeed",
    ),
)
def test_kimi_document_extract_keeps_attachment_source_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    files = _Files()
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"pdf"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="document", media_type="application/pdf"), current=True)

    rendered = adapter.render_message(message, _client(model))

    block = rendered["content"][1]["text"]
    assert '"source_type": "attachment"' in block
    assert '"trust"' not in block
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


def test_deepseek_image_upload_uses_finite_ttl_and_nested_file_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    monkeypatch.setattr("llm_providers.multimodal.adapter.time.time", lambda: 1_000_000)
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"png"), cache_dir=tmp_path)
    message = _Message(
        _attachment(kind="image", media_type="image/png"),
        current=True,
        content="",
    )
    client = _client("openai/deepseek-v4-flash-vision-exp", context_window=1_000_000)

    first = adapter.render_message(message, client)
    second = adapter.render_message(message, client)

    assert first == second
    assert first == {
        "role": "user",
        "content": [{"type": "file", "file": {"file_id": "remote-file"}}],
    }
    assert files.uploads == 1
    assert files.retrievals == 0
    assert files.purposes == ["user_data"]
    assert files.expires_after == [{"anchor": "created_at", "seconds": 86_400}]


def test_deepseek_cache_is_isolated_by_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    monkeypatch.setattr("llm_providers.multimodal.adapter.time.time", lambda: 1_000_000)
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"png"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="image", media_type="image/webp"), current=True)
    first_client = _client("openai/deepseek-v4-flash-vision-exp")
    second_client = LLMClient(
        ProviderSpec(
            model="openai/deepseek-v4-flash-vision-exp",
            api_key="other-secret",
            api_base="https://example.test",
            context_window=1_000_000,
        )
    )

    adapter.render_message(message, first_client)
    adapter.render_message(message, second_client)

    assert files.uploads == 2
    assert files.retrievals == 0
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_deepseek_unexpired_disk_cache_skips_remote_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    monkeypatch.setattr("llm_providers.multimodal.adapter.time.time", lambda: 1_000_000)
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    first_adapter = MultimodalInputAdapter(
        open_asset=lambda _id: BytesIO(b"jpeg"), cache_dir=tmp_path
    )
    message = _Message(_attachment(kind="image", media_type="image/jpeg"), current=True)
    client = _client("openai/deepseek-v4-flash-vision-exp")

    first = first_adapter.render_message(message, client)
    files.retrieve_error = True
    second_adapter = MultimodalInputAdapter(
        open_asset=lambda _id: BytesIO(b"jpeg"), cache_dir=tmp_path
    )
    second = second_adapter.render_message(message, client)

    assert second == first
    assert files.retrievals == 0
    assert files.uploads == 1


def test_deepseek_near_expiry_cache_reuploads_without_reusing_remote_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000
    files = _Files()
    monkeypatch.setattr("llm_providers.multimodal.adapter.time.time", lambda: now)
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"png"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="image", media_type="image/png"), current=True)
    client = _client("openai/deepseek-v4-flash-vision-exp")

    adapter.render_message(message, client)
    now = files.expires_at - 299
    files.expires_at = now + 86_400
    adapter.render_message(message, client)

    assert files.retrievals == 0
    assert files.uploads == 2


def test_deepseek_single_flight_uploads_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    monkeypatch.setattr("llm_providers.multimodal.adapter.time.time", lambda: 1_000_000)
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"png"), cache_dir=tmp_path)
    message = _Message(_attachment(kind="image", media_type="image/png"), current=True)
    client = _client("openai/deepseek-v4-flash-vision-exp")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: adapter.render_message(message, client), range(2)))

    assert results[0] == results[1]
    assert files.uploads == 1
    assert files.retrievals == 0


@pytest.mark.parametrize(
    ("kind", "media_type"),
    (
        ("document", "application/pdf"),
        ("document", "text/plain"),
        ("image", "image/gif"),
    ),
)
def test_deepseek_rejects_unsupported_attachment_types(
    tmp_path: Path,
    kind: str,
    media_type: str,
) -> None:
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"x"), cache_dir=tmp_path)

    with pytest.raises(LLMBadRequestError, match="attachment_type_unsupported"):
        adapter.render_message(
            _Message(_attachment(kind=kind, media_type=media_type), current=True),
            _client("openai/deepseek-v4-flash-vision-exp"),
        )


def test_deepseek_rejects_file_over_64_mib(tmp_path: Path) -> None:
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"x"), cache_dir=tmp_path)

    with pytest.raises(LLMBadRequestError, match="attachment_size_limit_exceeded"):
        adapter.render_message(
            _Message(
                _attachment(
                    kind="image",
                    media_type="image/png",
                    size_bytes=64 * 1024 * 1024 + 1,
                ),
                current=True,
            ),
            _client("openai/deepseek-v4-flash-vision-exp"),
        )


def test_deepseek_upload_failure_uses_safe_project_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _Files()
    files.create_error = True
    monkeypatch.setattr(
        "llm_providers.multimodal.adapter.OpenAI",
        lambda **_kwargs: SimpleNamespace(files=files),
    )
    adapter = MultimodalInputAdapter(open_asset=lambda _id: BytesIO(b"png"), cache_dir=tmp_path)

    with pytest.raises(LLMProviderError) as caught:
        adapter.render_message(
            _Message(_attachment(kind="image", media_type="image/png"), current=True),
            _client("openai/deepseek-v4-flash-vision-exp"),
        )

    assert "attachment_provider_upload_failed" in str(caught.value)
    assert "secret upstream body" not in str(caught.value)

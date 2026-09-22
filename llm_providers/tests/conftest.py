"""Explicit SDK test doubles shared by provider normalization tests."""

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest


@pytest.fixture
def patch_completion(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[..., Any]], None]:
    def install(fake: Callable[..., Any]) -> None:
        async def completion(**kwargs: Any) -> Any:
            value = fake(**kwargs)
            if not kwargs.get("stream"):
                return value

            async def chunks() -> AsyncIterator[Any]:
                try:
                    for chunk in value:
                        yield chunk
                finally:
                    close = getattr(value, "close", None)
                    if callable(close):
                        close()

            return chunks()

        monkeypatch.setattr("llm_providers.client.litellm.acompletion", completion)

    return install

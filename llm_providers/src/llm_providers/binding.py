"""Optional execution binding for dynamically configured language models."""

from collections.abc import Iterator
from contextlib import contextmanager

from .protocol import LanguageModel


@contextmanager
def bind_language_model(model: LanguageModel) -> Iterator[LanguageModel]:
    bind = getattr(model, "bind_execution", None)
    if callable(bind):
        with bind() as bound:
            yield bound
    else:
        yield model

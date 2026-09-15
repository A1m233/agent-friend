"""Shared credential-free model catalog for desktop and IM clients."""

from dataclasses import replace
from typing import Any

from llm_providers.connections import model_reference

from llm_providers import public_model_catalog

from .runtime import ConnectionRuntime


def connection_model_catalog(runtime: ConnectionRuntime | None) -> dict[str, Any]:
    if runtime is None:
        return public_model_catalog()
    directory = runtime.directory()
    models = tuple(
        replace(
            directory.model(model_reference(connection.id, model.id)),
            label=f"{connection.name} · {model.name}",
        )
        for connection in directory.connections.values()
        for model in connection.models
    )
    return public_model_catalog(models)

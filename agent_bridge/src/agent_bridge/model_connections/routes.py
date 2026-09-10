"""Owner-authorized configuration and ephemeral draft inspection operations."""

from typing import Any

from fastapi import FastAPI, Header, HTTPException
from llm_providers.connections import ModelDirectory
from llm_providers.connections.codec import decode_connections

from llm_providers import model_belongs_to_provider

from ..lifecycle import BridgeLifecycleController
from .inspection import inspect_draft
from .runtime import ConnectionRuntime


def register_routes(
    app: FastAPI, lifecycle: BridgeLifecycleController, runtime: ConnectionRuntime
) -> None:
    @app.post("/internal/model-connections/{action}", include_in_schema=False)
    def operation(
        action: str,
        body: dict[str, Any],
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        lifecycle.authorize(x_agent_friend_lifecycle_token)
        try:
            if action == "prepare":
                candidate = ModelDirectory(decode_connections(body["modelConnections"]))
                previous = runtime.directory()
                deleted = [
                    identity
                    for identity, connection in previous.connections.items()
                    if identity not in candidate.connections
                    or not {m.id for m in connection.models}.issubset(
                        {m.id for m in candidate.connections[identity].models}
                    )
                ]
                for key in ("chatModel", "memoryModel"):
                    candidate.model(body[key]["model"])
                    if not model_belongs_to_provider(body[key]["provider"], body[key]["model"]):
                        raise ValueError("invalid_default_selection")
                operation_id = body.get("operationId")
                if not isinstance(operation_id, str) or not 16 <= len(operation_id) <= 128:
                    raise ValueError("invalid_operation_id")
                return {"operationId": runtime.prepare(deleted, operation_id)}
            if action == "finish":
                runtime.finish(body["operationId"])
                return {"applied": True}
            if action == "inspect":
                return inspect_draft(body, runtime)
            raise HTTPException(status_code=404)
        except HTTPException:
            raise
        except (KeyError, ValueError) as error:
            code = (
                str(error)
                if str(error)
                in {
                    "connection_in_use",
                    "api_key_required",
                    "connection_mutation_pending",
                    "connection_operation_mismatch",
                }
                else "invalid_connection_configuration"
            )
            raise HTTPException(status_code=409, detail={"code": code}) from None
        except Exception as error:
            # No raw upstream body, request headers or keyring exception in the UI.
            raise HTTPException(status_code=400, detail={"code": type(error).__name__}) from None

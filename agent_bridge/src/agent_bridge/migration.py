"""Owner-only device migration control plane.

Routes in this module are reachable only with the supervisor lifecycle token. Secret
payloads must never be logged or exposed through the public WebView API.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent import BUILTIN_DEFAULT_PERSONA_ID

from .assembly import BridgeRuntime
from .lifecycle import BridgeLifecycleController
from .protocols.im.credentials import ImCredential


class ImCredentialPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    im_type: str
    bind_id: str
    app_id: str
    client_secret: str
    user_openid: str = ""
    extra: dict[str, Any] = {}


class ReplaceImCredentialsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credentials: list[ImCredentialPayload]
    state: dict[str, Any] | None = Field(default=None)


def register_migration_routes(
    app: FastAPI,
    runtime: BridgeRuntime,
    lifecycle: BridgeLifecycleController,
) -> None:
    router = APIRouter(prefix="/internal/migration", include_in_schema=False)

    def authorize(token: str | None) -> None:
        lifecycle.authorize(token)

    def require_im_runtime() -> Any:
        if runtime.im_runtime is None:
            raise HTTPException(status_code=503, detail="IM runtime is unavailable")
        return runtime.im_runtime

    @router.get("/inventory")
    def migration_inventory(
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        sessions = len(runtime.persistent_session_manager.list()) + len(
            runtime.persistent_session_manager.list(archived=True)
        )
        semantic_memories = 0
        episodic_memories = 0
        if runtime.memory is not None:
            semantic_memories = len(
                runtime.memory.store.list_semantic(owner_user_id="local", limit=1)
            )
            episodic_memories = len(
                runtime.memory.store.list_episodic(owner_user_id="local", limit=1)
            )
        persona_modified = False
        if runtime.persona_store is not None:
            effective = runtime.persona_store.load().content.strip()
            builtin = runtime.catalog.read_content(BUILTIN_DEFAULT_PERSONA_ID).strip()
            persona_modified = effective != builtin
        im_credentials = (
            len(runtime.im_runtime.migration_snapshot_credentials())
            if runtime.im_runtime is not None
            else 0
        )
        return {
            "sessions": sessions,
            "semanticMemories": semantic_memories,
            "episodicMemories": episodic_memories,
            "personaModified": persona_modified,
            "imCredentials": im_credentials,
        }

    @router.get("/im-credentials")
    def snapshot_im_credentials(
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        im_runtime = require_im_runtime()
        credentials = im_runtime.migration_snapshot_credentials()
        return {
            "credentials": [asdict(credential) for credential in credentials],
            "state": im_runtime.migration_snapshot_state(),
        }

    @router.post("/im-credentials/replace")
    async def replace_im_credentials(
        payload: ReplaceImCredentialsPayload,
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        credentials = [ImCredential(**item.model_dump()) for item in payload.credentials]
        await require_im_runtime().migration_replace_credentials(credentials, payload.state)
        return {"status": "ok", "count": len(credentials)}

    @router.post("/memory-index/rebuild")
    def rebuild_memory_index(
        x_agent_friend_lifecycle_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        authorize(x_agent_friend_lifecycle_token)
        if runtime.memory is None:
            raise HTTPException(status_code=503, detail="memory is unavailable")
        state = runtime.memory.rebuild_dense_index()
        if state is None:
            raise HTTPException(status_code=503, detail="dense memory index is unavailable")
        return {"status": "ok", "index": asdict(state)}

    app.include_router(router)

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from agent_bridge.assembly import BridgeRuntime
from agent_bridge.lifecycle import BridgeLifecycleController
from agent_bridge.migration import register_migration_routes
from agent_bridge.protocols.im.credentials import CredentialStore, ImCredential
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeImRuntime:
    def __init__(self, store: CredentialStore) -> None:
        self.store = store

    def migration_snapshot_credentials(self) -> list[ImCredential]:
        return self.store.list_all()

    async def migration_replace_credentials(self, credentials: list[ImCredential]) -> None:
        self.store.replace_all(credentials)


class FakeSessionManager:
    def list(self, *, archived: bool = False) -> list[object]:
        return [object()] if archived else [object(), object()]


class FakeMemoryStore:
    def list_semantic(self, **_kwargs: object) -> list[object]:
        return [object()]

    def list_episodic(self, **_kwargs: object) -> list[object]:
        return []


class FakePersonaStore:
    def load(self) -> SimpleNamespace:
        return SimpleNamespace(content="custom persona")


class FakeCatalog:
    def read_content(self, _persona_id: str) -> str:
        return "builtin persona"


def credential(secret: str = "secret") -> ImCredential:
    return ImCredential(
        im_type="qq",
        bind_id="binding-1",
        app_id="app-1",
        client_secret=secret,
        user_openid="user-1",
    )


def migration_client(tmp_path: Path) -> tuple[TestClient, CredentialStore]:
    store = CredentialStore(tmp_path / "credentials")
    runtime = cast(
        BridgeRuntime,
        SimpleNamespace(
            im_runtime=FakeImRuntime(store),
            agent_runtime=None,
            persistent_session_manager=FakeSessionManager(),
            memory=SimpleNamespace(store=FakeMemoryStore()),
            persona_store=FakePersonaStore(),
            catalog=FakeCatalog(),
        ),
    )
    lifecycle = BridgeLifecycleController(runtime, token="owner-token")
    app = FastAPI()
    register_migration_routes(app, runtime, lifecycle)
    return TestClient(app), store


def test_migration_routes_require_owner_token(tmp_path: Path) -> None:
    client, _store = migration_client(tmp_path)

    assert client.get("/internal/migration/im-credentials").status_code == 401
    assert (
        client.get(
            "/internal/migration/im-credentials",
            headers={"X-Agent-Friend-Lifecycle-Token": "wrong"},
        ).status_code
        == 401
    )


def test_im_credentials_round_trip_without_logging_secret(tmp_path: Path) -> None:
    client, store = migration_client(tmp_path)
    headers = {"X-Agent-Friend-Lifecycle-Token": "owner-token"}
    payload = {"credentials": [as_payload(credential())]}

    response = client.post(
        "/internal/migration/im-credentials/replace", json=payload, headers=headers
    )

    assert response.json() == {"status": "ok", "count": 1}
    assert store.list_all() == [credential()]
    snapshot = client.get("/internal/migration/im-credentials", headers=headers)
    assert snapshot.json() == payload


def test_inventory_uses_domain_records_instead_of_default_files(tmp_path: Path) -> None:
    client, store = migration_client(tmp_path)
    store.save(credential())

    response = client.get(
        "/internal/migration/inventory",
        headers={"X-Agent-Friend-Lifecycle-Token": "owner-token"},
    )

    assert response.json() == {
        "sessions": 3,
        "semanticMemories": 1,
        "episodicMemories": 0,
        "personaModified": True,
        "imCredentials": 1,
    }


def test_replace_all_keeps_original_when_staging_fails(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials")
    store.save(credential("original"))
    replacement = credential("replacement")

    # Duplicate identities make the verified staging inventory differ from the request.
    try:
        store.replace_all([replacement, replacement])
    except ValueError:
        pass
    else:
        raise AssertionError("replace_all should reject a non-verifiable staging set")

    assert store.list_all() == [credential("original")]


def as_payload(value: ImCredential) -> dict[str, object]:
    return {
        "im_type": value.im_type,
        "bind_id": value.bind_id,
        "app_id": value.app_id,
        "client_secret": value.client_secret,
        "user_openid": value.user_openid,
        "extra": value.extra,
    }

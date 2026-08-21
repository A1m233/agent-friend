from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agent.skills import (
    FirstPartySkillStore,
    FirstPartySkillUpdateService,
    SkillCatalogService,
)
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.skills import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_catalog_route_returns_metadata_without_skill_body(tmp_path: Path) -> None:
    personal = tmp_path / "skills"
    package = personal / "route-skill"
    package.mkdir(parents=True)
    secret_body = "BODY MUST NOT BE RETURNED"
    (package / "SKILL.md").write_text(
        "---\nname: route-skill\ndescription: Route test\n---\n\n" + secret_body,
        encoding="utf-8",
    )
    service = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    service.initialize()
    updater = FirstPartySkillUpdateService(
        store=FirstPartySkillStore(personal),
        catalog=service,
        product_version=None,
    )
    runtime = BridgeRuntime(
        settings=MagicMock(),
        persistent_store=MagicMock(),
        transient_store=MagicMock(),
        persistent_session_manager=MagicMock(),
        catalog=MagicMock(),
        tool_registry=MagicMock(),
        prompt_builder_factory=MagicMock(),
        default_persona="default",
        default_model="test/model",
        skill_service=service,
        first_party_skill_updater=updater,
    )
    app = FastAPI()
    register_routes(app, runtime)

    response = TestClient(app).get("/v1/skills")

    assert response.status_code == 200
    payload = response.json()
    assert payload["generation"] == 1
    assert any(item["name"] == "route-skill" for item in payload["skills"])
    assert payload["firstPartyUpdates"] == {
        "enabled": False,
        "source": "baseline",
        "activeSequence": None,
        "activePackageSha256": None,
        "highestSeenSequence": 0,
        "lastCheckedAt": None,
        "lastResult": "host_version_unavailable",
        "lastDetail": None,
    }
    assert secret_body not in response.text

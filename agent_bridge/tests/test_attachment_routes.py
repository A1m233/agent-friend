from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.attachments import register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import AttachmentStore


def test_attachment_routes_upload_read_and_release_draft(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    runtime = cast(BridgeRuntime, SimpleNamespace(attachment_store=store))
    app = FastAPI()
    register_routes(app, runtime)
    client = TestClient(app)

    uploaded = client.post(
        "/v1/attachments/drafts",
        content=b"\x89PNG\r\n\x1a\npayload",
        headers={
            "Content-Type": "image/png",
            "X-Attachment-Filename": "screen.png",
        },
    )

    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["contentUrl"].startswith("/v1/attachments/drafts/")
    assert body["expiresAt"]
    asset_id = body["attachment"]["asset_id"]
    assert client.get(f"/v1/attachments/assets/{asset_id}").json()["available"] is True
    assert client.get(body["contentUrl"]).content == b"\x89PNG\r\n\x1a\npayload"

    released = client.delete(f"/v1/attachments/drafts/{body['draftId']}")

    assert released.status_code == 204
    assert client.get(body["contentUrl"]).status_code == 404

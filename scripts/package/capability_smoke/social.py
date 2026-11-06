"""Offline account, QR and video-comment capability probe in the frozen runtime."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import parse_qs


def run_social_smoke() -> dict[str, bool]:
    import httpx
    import qrcode
    from agent.tools.builtin.social_platforms.auth import SocialAuthInteraction
    from agent.tools.builtin.social_platforms.connectors.bilibili import BilibiliSdkApi
    from agent.tools.builtin.social_platforms.connectors.bilibili_auth import (
        BilibiliAuthHandler,
        BilibiliCredentialStore,
        LocalQrPresenter,
    )
    from agent.tools.builtin.social_platforms.connectors.bilibili_social import (
        BilibiliSocialApi,
        comment_record,
    )
    from bilibili_api import Credential, comment, session, user, video

    class Backend:
        value: str | None = None

        def get(self, _name: str) -> object | None:
            return SimpleNamespace(value=self.value) if self.value else None

        def status(self, _name: str) -> object:
            return SimpleNamespace(configured=bool(self.value), error=None)

        def set(self, _name: str, value: str) -> None:
            self.value = value

        def delete(self, _name: str) -> None:
            self.value = None

    class Picture:
        def to_file(self, path: str) -> None:
            qrcode.make("https://fixture.invalid/qr").save(path)

    challenges = []
    with LocalQrPresenter().show(
        Picture(), interaction=SocialAuthInteraction(challenges.append, lambda: None)
    ):
        assert challenges and challenges[0].content.startswith(b"\x89PNG")
    credential_store = BilibiliCredentialStore(Backend())
    credential_store.save(Credential(sessdata="fixture-only", bili_jct="fixture-csrf"))
    sdk = BilibiliSdkApi()
    auth = BilibiliAuthHandler(sdk, store=credential_store)
    assert sdk.authenticated
    # Instantiate the SDK's newly used submodules in the packaged environment.
    video.Video(aid=123)
    comment.Comment(123, comment.CommentResourceType.VIDEO, 11)
    user.User(456)
    assert callable(session.get_replies) and callable(session.get_at)
    assert (
        comment_record({"rpid": 11, "content": {"message": "fixture"}})["source_type"]
        == "external_comment"
    )
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"rpid": 77}})

    real_client = httpx.Client
    # Patching the transport of this one synchronous call leaves all network disabled.
    httpx.Client = lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond))  # type: ignore[misc,assignment]
    try:
        result = BilibiliSocialApi(sdk).post(
            {
                "aid": "123",
                "bvid": "BV1234567890",
                "generation": sdk.credential_generation,
                "text": "@fixture 测试",
                "mentions": [{"uid": "456", "name": "fixture"}],
                "root_id": "10",
                "parent_id": "11",
            }
        )
    finally:
        httpx.Client = real_client  # type: ignore[misc]
    assert (
        result["status"] == "accepted"
        and result["public_visibility"] == "unknown"
        and len(captured) == 1
    )
    assert json.loads(parse_qs(captured[0].content.decode())["at_name_to_mid"][0]) == {
        "fixture": 456
    }
    auth.logout()
    assert not sdk.authenticated and credential_store.load() is None
    from datetime import UTC, datetime

    from agent.message_exposure import MessageExposureStore, content_version

    with TemporaryDirectory() as temp:
        store = MessageExposureStore(Path(temp) / "display.db")
        store.record("s", "m", content_version("fixture"), "full", observed_at=datetime.now(UTC))
        assert store.changes(0)["items"]
    return {
        "social_qr": True,
        "social_credentials": True,
        "social_video_comment": True,
        "message_display_store": True,
    }

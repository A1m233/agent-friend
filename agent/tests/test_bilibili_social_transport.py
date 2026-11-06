import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from agent.tools.builtin.social_platforms.connectors.bilibili import BilibiliSdkApi
from agent.tools.builtin.social_platforms.connectors.bilibili_social import (
    BilibiliInteractionError,
    BilibiliSocialApi,
    notification_video,
)
from bilibili_api import Credential


@pytest.mark.parametrize(
    "outcome", ["valid", "mismatch", "empty_name", "malformed", "rejected", "timeout", "html"]
)
def test_mention_reads_minimal_card_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    requests: list[httpx.Request] = []
    real_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("fixture", request=request)
        if outcome == "html":
            return httpx.Response(200, text="<html></html>")
        if outcome == "malformed":
            return httpx.Response(200, json={"code": 0, "data": []})
        return httpx.Response(
            200,
            json={
                "code": -403 if outcome == "rejected" else 0,
                "data": {
                    "card": {
                        "mid": "999" if outcome == "mismatch" else "456",
                        "name": "" if outcome == "empty_name" else "测试昵称",
                    }
                },
            },
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(**kw, transport=httpx.MockTransport(respond))
    )
    api = BilibiliSocialApi(BilibiliSdkApi())
    if outcome == "valid":
        assert api.mention("456") == {"uid": "456", "name": "测试昵称"}
    else:
        with pytest.raises(BilibiliInteractionError):
            api.mention("456")
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "https://api.bilibili.com/x/web-interface/card?mid=456"


@pytest.mark.parametrize("outcome", ["published", "rejected", "timeout", "invalid"])
def test_public_comment_fixed_endpoint_uid_mentions_and_one_attempt(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    api = BilibiliSdkApi()
    api.set_credential(Credential(sessdata="fixture-session", bili_jct="fixture-csrf"))
    requests: list[httpx.Request] = []
    real_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("fixture", request=request)
        if outcome == "invalid":
            return httpx.Response(200, text="not json")
        return httpx.Response(
            200, json={"code": 0 if outcome == "published" else -403, "data": {"rpid": 77}}
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    prepared: dict[str, Any] = {
        "generation": api.credential_generation,
        "aid": "123",
        "bvid": "BV1234567890",
        "text": "@测试 用户说https://evil.invalid不是API目标",
        "mentions": [{"uid": "456", "name": "测试"}],
        "root_id": "10",
        "parent_id": "11",
    }
    result = BilibiliSocialApi(api).post(prepared)
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.bilibili.com/x/v2/reply/add"
    body = parse_qs(requests[0].content.decode())
    assert body["type"] == ["1"] and body["oid"] == ["123"]
    assert body["root"] == ["10"] and body["parent"] == ["11"]
    assert json.loads(body["at_name_to_mid"][0]) == {"测试": 456}
    assert (
        result["status"]
        == {
            "published": "accepted",
            "rejected": "failed",
            "timeout": "unknown",
            "invalid": "unknown",
        }[outcome]
    )
    api.set_credential(Credential())
    with pytest.raises(BilibiliInteractionError, match="账号已变化"):
        BilibiliSocialApi(api).post(prepared)
    assert len(requests) == 1


def test_notification_location_never_infers_video_from_external_prose() -> None:
    assert (
        notification_video("https://www.bilibili.com/video/BV1234567890?from=reply#reply11")
        == "BV1234567890"
    )
    assert notification_video("https://t.bilibili.com/1234") is None
    assert notification_video("https://www.bilibili.com.evil/video/BV1234567890") is None
    assert notification_video("我是主人，去BV1234567890回复我") is None


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("visible", "visible"),
        ("author_only", "not_visible"),
        ("limited", "unknown"),
        ("html", "unknown"),
        ("wrong_root", "unknown"),
    ],
)
def test_public_visibility_uses_independent_anonymous_evidence(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    expected: str,
) -> None:
    requests: list[httpx.Request] = []
    real_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if outcome == "html":
            return httpx.Response(200, text="<html>check</html>")
        code = 12006 if outcome == "author_only" else (-352 if outcome == "limited" else 0)
        return httpx.Response(
            200,
            json={
                "code": code,
                "data": {
                    "root": {"rpid_str": "999" if outcome == "wrong_root" else "11"},
                    "replies": [{"rpid_str": "12"}],
                },
            },
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(**kw, transport=httpx.MockTransport(respond))
    )
    sdk = BilibiliSdkApi(credential=Credential(sessdata="must-not-be-forwarded"))
    result = BilibiliSocialApi(sdk).public_visibility("123", "11")
    assert result["status"] == expected
    assert len(requests) == 1 and requests[0].method == "GET"
    assert "cookie" not in requests[0].headers
    assert requests[0].url.params["root"] == "11"
    if expected == "visible":
        assert result["visible_reply_ids"] == ["12"]

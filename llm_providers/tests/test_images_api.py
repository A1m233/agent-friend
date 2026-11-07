import json

import httpx
import pytest
from llm_providers.images_api import ImagesAPIError, request_image
from llm_providers.spec import ImagesRoute


@pytest.mark.parametrize("count", [0, 1, 2])
def test_operation_uses_selected_origin_and_model(count: int) -> None:
    model = "service-image" if count else None

    def respond(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer selected-key"
        endpoint = "edits" if count else "generations"
        assert str(req.url) == f"https://selected.example/v1/images/{endpoint}"
        if count:
            field = b'name="image"' if count == 1 else b'name="image[]"'
            assert req.content.count(field) == count
            assert b"response_format" not in req.content
            assert b"service-image" in req.content and b"transparent" in req.content
            for i in range(count):
                assert f"reference-{i}".encode() in req.content
        else:
            assert json.loads(req.content) == {"prompt": "draw"}
        return httpx.Response(200, json={"data": [{"b64_json": "cmVzdWx0"}]})

    refs = [(f"{i}.png", f"reference-{i}".encode(), "image/png") for i in range(count)]
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert (
            request_image(
                client,
                ImagesRoute("https://selected.example/v1/", model),
                "selected-key",
                "draw",
                refs,
                bool(count),
            )
            == b"result"
        )


@pytest.mark.parametrize(
    ("status", "payload", "code"),
    [
        (401, {}, "image_access_denied"),
        (403, {}, "image_access_denied"),
        (429, {}, "image_rate_limited"),
        (404, {}, "image_endpoint_unavailable"),
        (405, {}, "image_endpoint_unavailable"),
        (404, {"error": {"code": "model_not_found"}}, "image_model_required"),
        (400, {"error": {"param": "model"}}, "image_model_required"),
        (503, {}, "image_upstream_failed"),
        (307, {}, "image_request_rejected"),
        (200, {"data": [{"url": "https://other.invalid/image"}]}, "image_result_missing"),
        (200, {"data": [{"b64_json": "?"}]}, "image_invalid"),
        (200, {"data": [{}, {}]}, "image_result_missing"),
    ],
)
def test_failures_do_not_retry_or_follow_another_server(
    status: int, payload: dict[str, object], code: str
) -> None:
    calls = []

    def respond(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.host)
        return httpx.Response(status, headers={"location": "https://other.invalid/"}, json=payload)

    with (
        httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client,
        pytest.raises(ImagesAPIError) as err,
    ):
        request_image(
            client, ImagesRoute("https://selected.example/v1", None), "secret", "draw", [], False
        )
    assert err.value.code == code
    assert calls == ["selected.example"]


def test_generation_remains_available_on_generation_only_endpoint() -> None:
    def respond(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/edits"):
            return httpx.Response(405)
        assert json.loads(req.content)["response_format"] == "b64_json"
        return httpx.Response(200, json={"data": [{"b64_json": "b2s="}]})

    route = ImagesRoute("https://selected.example/v1", "dall-e-2")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ImagesAPIError) as err:
            request_image(
                client, route, "fixture", "edit", [("input.png", b"input", "image/png")], False
            )
        assert err.value.code == "image_endpoint_unavailable" and "编辑" in str(err.value)
        assert request_image(client, route, "fixture", "draw", [], False) == b"ok"

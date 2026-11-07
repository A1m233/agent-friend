"""Standalone Images requests for the selected compatible upstream."""

from __future__ import annotations

import json
from collections.abc import Sequence

import httpx

from .image_output import MAX_OUTPUT_IMAGE_BYTES, decode_image_result
from .spec import ImagesRoute

_RESPONSE_LIMIT = 4 * ((MAX_OUTPUT_IMAGE_BYTES + 2) // 3) + 256 * 1024


class ImagesAPIError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


def _rejection(response: httpx.Response, operation: str) -> ImagesAPIError:
    body = bytearray()
    for chunk in response.iter_bytes(chunk_size=4096):
        body.extend(chunk)
        if len(body) >= 16384:
            break
    try:
        data = json.loads(body)
        err = data.get("error") if isinstance(data, dict) else None
    except (ValueError, UnicodeError):
        err = None
    code = str(err.get("code", "")) if isinstance(err, dict) else ""
    message = str(err.get("message", "")).lower() if isinstance(err, dict) else ""
    param = err.get("param") if isinstance(err, dict) else None
    status = response.status_code
    if status in {401, 403}:
        return ImagesAPIError(
            "image_access_denied", "当前连接未获图片接口访问权限，请检查凭据和账号权限。"
        )
    if status == 429:
        return ImagesAPIError("image_rate_limited", "图片接口限流或额度不足。")
    if code in {"model_not_found", "invalid_model", "missing_model"} or (
        status in {400, 404, 422}
        and (param == "model" or ("model" in message and "required" in message))
    ):
        return ImagesAPIError(
            "image_model_required", "请在当前连接的“图片模型”中指定服务支持的模型 ID。"
        )
    if status in {404, 405, 501}:
        return ImagesAPIError(
            "image_endpoint_unavailable", f"当前连接的图片{operation}接口不可用。"
        )
    if status >= 500:
        return ImagesAPIError("image_upstream_failed", "图片服务暂时失败，结果未知。")
    return ImagesAPIError(
        "image_request_rejected", f"图片{operation}请求被当前连接拒绝（HTTP {status}）。"
    )


def request_image(
    client: httpx.Client,
    route: ImagesRoute,
    key: str,
    prompt: str,
    inputs: Sequence[tuple[str, bytes, str]],
    transparent: bool,
) -> bytes:
    fields = {"prompt": prompt}
    # GPT Image returns base64 directly and can reject the legacy response_format field.
    if route.model in {"dall-e-2", "dall-e-3"}:
        fields["response_format"] = "b64_json"
    if route.model:
        fields["model"] = route.model
    if transparent:
        fields["background"] = "transparent"
    if inputs:
        field = "image" if len(inputs) == 1 else "image[]"
        files = [(field, item) for item in inputs]
        request = client.build_request(
            "POST", route.api_base.rstrip("/") + "/images/edits", data=fields, files=files
        )
    else:
        request = client.build_request(
            "POST", route.api_base.rstrip("/") + "/images/generations", json=fields
        )
    request.headers["Authorization"] = f"Bearer {key}"
    try:
        response = client.send(request, stream=True, follow_redirects=False)
        try:
            if not response.is_success:
                raise _rejection(response, "编辑" if inputs else "生成")
            body = bytearray()
            for part in response.iter_bytes(chunk_size=65536):
                if len(body) + len(part) > _RESPONSE_LIMIT:
                    raise ImagesAPIError("image_size_exceeded", "上游图片响应超过大小限制。")
                body.extend(part)
        finally:
            response.close()
    except httpx.HTTPError as exc:
        raise ImagesAPIError("image_transport_failed", "图片请求中断，结果未知。") from exc
    try:
        data = json.loads(body)["data"]
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError("expected one image")
        encoded = data[0]["b64_json"]
    except (TypeError, KeyError, ValueError, UnicodeError) as exc:
        raise ImagesAPIError("image_result_missing", "上游没有返回一张可用的图片。") from exc
    result = decode_image_result("", 0, encoded, "completed", 0)
    if result.error_code:
        raise ImagesAPIError(result.error_code, "上游返回了无效或过大的图片。")
    return result.data

"""火山引擎 OpenAPI Sign V4 + RTC AIGC ``StartVoiceChat`` / ``StopVoiceChat`` 客户端。

本期手写 HMAC-SHA256 实现 Sign V4，**不**引入 ``volcengine-python-sdk``——
那个 SDK 覆盖火山所有产品线，仅为本期 1 个 OpenAPI 调用引入太重。

签名算法基于 AWS Signature V4 同源演化（火山在 region / service / 算法常量上
有自己的命名）。算法稳定，单元测试用固定 timestamp + 已知正确签名做向量
回归（详见 ``tests/unit/test_rtc_openapi_sign.py``）。

详见 docs/requirements/007-voice-call/design.md §4.5。
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from ..errors import (
    VolcAuthError,
    VolcRateLimitError,
    VolcRoomCreateError,
    VolcUnreachableError,
)

VOLC_HOST = "rtc.volcengineapi.com"
VOLC_REGION = "cn-north-1"
VOLC_SERVICE = "rtc"
VOLC_API_VERSION = "2024-12-01"
SIGN_ALGORITHM = "HMAC-SHA256"


def _hash_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _canonical_uri(path: str) -> str:
    """火山 OpenAPI 路径恒为 ``/``。保留扩展位以便未来支持别的路径。"""
    return path or "/"


def _canonical_query(query: Mapping[str, str]) -> str:
    """按 key 排序、URL encode（RFC 3986 严格），用 ``&`` 拼接。"""
    parts: list[str] = []
    for key in sorted(query.keys()):
        value = query[key]
        parts.append(f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}")
    return "&".join(parts)


def _canonical_headers(headers: Mapping[str, str], signed_keys: list[str]) -> tuple[str, str]:
    """返回 ``(canonical_headers_block, signed_headers_string)``。

    - canonical_headers_block：每行 ``<lowercase_name>:<trimmed_value>\\n``，按 name 排序
    - signed_headers_string：分号拼接、lowercase、按 name 排序
    """
    lower_map = {k.lower(): v.strip() for k, v in headers.items() if k.lower() in signed_keys}
    sorted_keys = sorted(lower_map.keys())
    canonical = "".join(f"{k}:{lower_map[k]}\n" for k in sorted_keys)
    return canonical, ";".join(sorted_keys)


def sign_v4(
    *,
    method: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str = VOLC_REGION,
    service: str = VOLC_SERVICE,
    now: datetime | None = None,
) -> dict[str, str]:
    """计算火山 OpenAPI V4 签名所需 header。

    Args:
        method: HTTP 方法（如 ``"POST"``）。
        query: query string 参数（如 ``{"Action": "StartVoiceChat", "Version": "2024-12-01"}``）。
        headers: 已有 headers（至少含 ``Host`` + ``Content-Type``）；本函数**不**修改它，
            只读取参与签名的 header（host / content-type / x-date / x-content-sha256）。
        body: 请求 body 字节流（用于 SHA256）。
        access_key: 火山 IAM AK。
        secret_key: 火山 IAM SK。
        region: 默认 ``cn-north-1``。
        service: 默认 ``rtc``。
        now: 签名时间；不传则用当前 UTC 时间（**测试时务必注入**）。

    Returns:
        一个 dict，含需要追加到 request headers 的：
        ``X-Date``（必填）、``X-Content-Sha256``（必填）、``Authorization``（必填）。
    """
    ts = now or datetime.now(UTC)
    ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)

    x_date = ts.strftime("%Y%m%dT%H%M%SZ")
    short_date = ts.strftime("%Y%m%d")
    payload_hash = _hash_sha256(body)

    # canonical headers 至少包含：host / content-type / x-date / x-content-sha256
    augmented = {
        **headers,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
    }
    signed_keys = ["content-type", "host", "x-content-sha256", "x-date"]

    canonical_headers, signed_headers = _canonical_headers(augmented, signed_keys)
    canonical_request = "\n".join(
        [
            method.upper(),
            _canonical_uri("/"),
            _canonical_query(query),
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            SIGN_ALGORITHM,
            x_date,
            credential_scope,
            _hash_sha256(canonical_request.encode("utf-8")),
        ]
    )

    k_date = _hmac_sha256(secret_key.encode("utf-8"), short_date.encode("utf-8"))
    k_region = _hmac_sha256(k_date, region.encode("utf-8"))
    k_service = _hmac_sha256(k_region, service.encode("utf-8"))
    k_signing = _hmac_sha256(k_service, b"request")
    signature = _hmac_sha256(k_signing, string_to_sign.encode("utf-8")).hex()

    authorization = (
        f"{SIGN_ALGORITHM} "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return {
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": authorization,
    }


@dataclass(frozen=True)
class StartVoiceChatResult:
    """``StartVoiceChat`` 调用结果（成功路径）。

    Attributes:
        task_id: 火山的 ``TaskId``，等同 voice_bridge 自己的 ``call_id``（同值）。
        raw_response: 火山 OpenAPI 完整响应 dict（调试 / 日志用）。
    """

    task_id: str
    raw_response: dict[str, Any]


class VolcRtcClient:
    """对火山 RTC OpenAPI 的薄封装。

    本期只用 2 个 API：``StartVoiceChat`` / ``StopVoiceChat``。

    所有方法都是 async（基于 :class:`httpx.AsyncClient`），错误统一转为
    :mod:`voice_bridge.errors` 里的异常类型，不向调用方泄漏 httpx /
    火山特有的错误结构。
    """

    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        host: str = VOLC_HOST,
        api_version: str = VOLC_API_VERSION,
        region: str = VOLC_REGION,
        service: str = VOLC_SERVICE,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._host = host
        self._api_version = api_version
        self._region = region
        self._service = service
        self._http_client = http_client  # 测试时注入 mock；生产期用 ``async with`` 自建

    async def start_voice_chat(
        self, body: dict[str, Any], *, now: datetime | None = None
    ) -> StartVoiceChatResult:
        """调火山 ``StartVoiceChat`` 创建 RTC 房间 + 拉起 AIGC 任务。

        Args:
            body: 完整 body（由 :func:`voice_bridge.rtc.scenes.build_scenes` 组装；
                含 ``AppId`` / ``RoomId`` / ``TaskId`` / ``AgentConfig`` / ``Config``）。
            now: 签名时间；不传则用当前 UTC（测试用）。

        Returns:
            :class:`StartVoiceChatResult`。``task_id`` 与传入 body 的 ``TaskId`` 同值。

        Raises:
            VolcAuthError: 401/403 / 签名错误 / AK SK 错。
            VolcRateLimitError: 429 / 火山限流。
            VolcRoomCreateError: 火山业务参数错（4xx 非鉴权）/ 房间创建失败。
            VolcUnreachableError: 网络错误（DNS / 连接超时等）。
        """
        task_id_in = body.get("TaskId", "")
        result = await self._invoke(action="StartVoiceChat", body=body, now=now)
        return StartVoiceChatResult(task_id=task_id_in, raw_response=result)

    async def stop_voice_chat(
        self,
        *,
        app_id: str,
        room_id: str,
        task_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """调火山 ``StopVoiceChat`` 释放 RTC 资源。

        幂等：火山对已停止的 task 重复调 stop 返回错误也 OK——voice_bridge
        控制平面层把任何 ``stop_voice_chat`` 抛出 :class:`VolcRoomCreateError`
        都吞掉（详见 routes/control.py），让挂断接口对 surface 永远幂等。
        """
        body = {"AppId": app_id, "RoomId": room_id, "TaskId": task_id}
        return await self._invoke(action="StopVoiceChat", body=body, now=now)

    async def _invoke(
        self, *, action: str, body: dict[str, Any], now: datetime | None
    ) -> dict[str, Any]:
        """核心：组 query + 签名 + 发请求 + 解析错误。"""
        import json

        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        query = {"Action": action, "Version": self._api_version}
        headers = {
            "Host": self._host,
            "Content-Type": "application/json",
        }
        sign_headers = sign_v4(
            method="POST",
            query=query,
            headers=headers,
            body=body_bytes,
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            service=self._service,
            now=now,
        )
        all_headers = {**headers, **sign_headers}
        url = f"https://{self._host}/?Action={action}&Version={self._api_version}"

        try:
            client = self._http_client or httpx.AsyncClient(timeout=30.0)
            close_after = self._http_client is None
            try:
                resp = await client.post(url, content=body_bytes, headers=all_headers)
            finally:
                if close_after:
                    await client.aclose()
        except httpx.RequestError as e:
            raise VolcUnreachableError(detail=str(e)) from e

        return self._parse_response(action, resp)

    def _parse_response(self, action: str, resp: httpx.Response) -> dict[str, Any]:
        """统一解析火山 OpenAPI 响应；失败时转 voice_bridge 错误类型。"""
        try:
            data = resp.json()
        except ValueError as e:
            raise VolcRoomCreateError(
                detail=f"{action} 响应解析失败 (status={resp.status_code})"
            ) from e

        meta = data.get("ResponseMetadata", {}) if isinstance(data, dict) else {}
        error = meta.get("Error") if isinstance(meta, dict) else None

        # 成功路径：火山返回 ``{"ResponseMetadata": {...}, "Result": "ok"}``
        if not error and resp.is_success:
            return data if isinstance(data, dict) else {}

        # 失败路径：根据 status / error code 分类
        if resp.status_code in (401, 403):
            raise VolcAuthError(detail=f"{action} 鉴权失败: {error}")
        if resp.status_code == 429:
            raise VolcRateLimitError(detail=f"{action} 被限流: {error}")
        # 业务错误（4xx 非鉴权 / 5xx）→ 创建失败
        raise VolcRoomCreateError(
            detail=f"{action} 失败 (status={resp.status_code}): {error or data}"
        )

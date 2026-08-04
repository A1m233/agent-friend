"""Fail-closed read-only adapter for the pinned ``xiaohongshu-mcp`` release."""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from agent.paths import user_data_dir

from ..models import (
    SocialAccessRestrictedError,
    SocialAuthExpiredError,
    SocialAuthRequiredError,
    SocialConnectorIncompatibleError,
    SocialConnectorUnavailableError,
    SocialContentNotFoundError,
    SocialContentType,
    SocialEvidence,
    SocialNetworkError,
    SocialPlatform,
    SocialPlatformError,
    SocialProviderError,
    SocialReadResponse,
    SocialResolvedUrl,
    SocialResourceLocator,
    SocialSearchCandidate,
    SocialSearchRequest,
    SocialSearchResponse,
)
from .xiaohongshu_artifacts import (
    XIAOHONGSHU_VERSION,
    XiaohongshuArtifact,
    current_xiaohongshu_artifacts,
    default_xiaohongshu_artifact_dir,
    verify_xiaohongshu_artifact,
)

_NOTE_URL = "https://www.xiaohongshu.com/explore/{feed_id}"
_COUNT_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(万|亿)?")
_ACCESS_MARKERS = ("412", "验证码", "风控", "安全验证", "访问频繁", "操作频繁")
_NOT_FOUND_MARKERS = ("没有捕获到 feed", "不存在", "已删除", "not found", "404")
_AUTH_MARKERS = ("未登录", "登录状态", "cookie", "cookies")
XIAOHONGSHU_DEFAULT_MCP_TIMEOUT_SECONDS = 45.0
XIAOHONGSHU_DEFAULT_SEARCH_MCP_TIMEOUT_SECONDS = 75.0


class _XhsOperation(Enum):
    CHECK_LOGIN_STATUS = "check_login_status"
    SEARCH_FEEDS = "search_feeds"
    GET_FEED_DETAIL = "get_feed_detail"


@dataclass(frozen=True)
class XiaohongshuNoteLocator:
    feed_id: str
    xsec_token: str
    platform: SocialPlatform = field(init=False, default="xiaohongshu")
    content_type: SocialContentType = field(init=False, default="note")


@dataclass(frozen=True)
class McpToolDefinition:
    name: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class McpCallResult:
    text: str
    is_error: bool


class XiaohongshuMcpTransport(Protocol):
    def list_tools(self, endpoint: str) -> tuple[McpToolDefinition, ...]: ...

    def call_tool(
        self, endpoint: str, name: str, arguments: Mapping[str, Any]
    ) -> McpCallResult: ...


class OfficialMcpTransport:
    """Official MCP SDK transport; each operation owns a short-lived session."""

    def __init__(
        self,
        *,
        read_timeout_seconds: float = XIAOHONGSHU_DEFAULT_MCP_TIMEOUT_SECONDS,
        search_timeout_seconds: float | None = XIAOHONGSHU_DEFAULT_SEARCH_MCP_TIMEOUT_SECONDS,
    ) -> None:
        if read_timeout_seconds <= 0 or (
            search_timeout_seconds is not None and search_timeout_seconds <= 0
        ):
            raise ValueError("Xiaohongshu MCP timeouts must be positive")
        self._read_timeout_seconds = read_timeout_seconds
        self._search_timeout_seconds = search_timeout_seconds or read_timeout_seconds

    def list_tools(self, endpoint: str) -> tuple[McpToolDefinition, ...]:
        return _run_async(self._list_tools(endpoint))

    def call_tool(self, endpoint: str, name: str, arguments: Mapping[str, Any]) -> McpCallResult:
        return _run_async(self._call_tool(endpoint, name, dict(arguments)))

    def _tool_timeout_seconds(self, name: str) -> float:
        if name == _XhsOperation.SEARCH_FEEDS.value:
            return self._search_timeout_seconds
        return self._read_timeout_seconds

    async def _list_tools(self, endpoint: str) -> tuple[McpToolDefinition, ...]:
        async with (
            streamable_http_client(endpoint) as (read_stream, write_stream),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=self._read_timeout_seconds,
            ) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
        return tuple(
            McpToolDefinition(name=tool.name, input_schema=dict(tool.input_schema))
            for tool in result.tools
        )

    async def _call_tool(
        self, endpoint: str, name: str, arguments: dict[str, Any]
    ) -> McpCallResult:
        timeout_seconds = self._tool_timeout_seconds(name)
        async with (
            streamable_http_client(endpoint) as (read_stream, write_stream),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timeout_seconds,
            ) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments=arguments)
        content = getattr(result, "content", ())
        texts = [
            item.text
            for item in content
            if getattr(item, "type", None) == "text" and isinstance(item.text, str)
        ]
        return McpCallResult(
            text="\n".join(texts),
            is_error=bool(getattr(result, "is_error", False)),
        )


class XiaohongshuProcess:
    """Own the loopback server/login child processes and their private endpoint."""

    def __init__(
        self,
        *,
        artifact_dir: Path | None = None,
        cookies_path: Path | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        allocate_port: Callable[[], int] | None = None,
        verify_artifacts: bool = True,
        artifacts: tuple[XiaohongshuArtifact, XiaohongshuArtifact] | None = None,
    ) -> None:
        self._artifact_dir = artifact_dir or default_xiaohongshu_artifact_dir()
        self._cookies_path = cookies_path or (
            user_data_dir() / "connectors" / "xiaohongshu" / "cookies.json"
        )
        self._popen = popen
        self._allocate_port = allocate_port or _allocate_loopback_port
        self._verify_artifacts = verify_artifacts
        self._artifacts = artifacts
        self._lock = threading.RLock()
        self._server: subprocess.Popen[bytes] | None = None
        self._login: subprocess.Popen[bytes] | None = None
        self._endpoint: str | None = None

    @property
    def has_login_material(self) -> bool:
        return self._cookies_path.is_file() and self._cookies_path.stat().st_size > 0

    def ensure_started(self) -> str:
        with self._lock:
            if self._server is not None and self._server.poll() is None and self._endpoint:
                return self._endpoint
            self._clear_server()
            server_artifact, _ = self._selected_artifacts()
            binary = self._verified_path(server_artifact)
            self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
            port = self._allocate_port()
            endpoint = f"http://127.0.0.1:{port}/mcp"
            command = [str(binary), f"-port=127.0.0.1:{port}", "-headless=true"]
            try:
                self._server = self._popen(
                    command,
                    cwd=str(binary.parent),
                    env=self._process_env(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=_no_window_flags(),
                )
            except OSError as exc:
                raise SocialConnectorUnavailableError(
                    "xiaohongshu-mcp could not be started"
                ) from exc
            self._endpoint = endpoint
            return endpoint

    def run_login(self) -> int:
        with self._lock:
            self._stop_server()
            if self._login is not None and self._login.poll() is None:
                raise SocialProviderError("Xiaohongshu login is already running")
            _, login_artifact = self._selected_artifacts()
            binary = self._verified_path(login_artifact)
            self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._login = self._popen(
                    [str(binary)],
                    cwd=str(binary.parent),
                    env=self._process_env(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=_no_window_flags(),
                )
            except OSError as exc:
                raise SocialConnectorUnavailableError(
                    "xiaohongshu login program could not be started"
                ) from exc
            process = self._login
        try:
            return process.wait()
        finally:
            with self._lock:
                if self._login is process:
                    self._login = None

    def close(self) -> None:
        with self._lock:
            self._stop_server()
            if self._login is not None:
                _stop_owned_process(self._login)
                self._login = None

    def _verified_path(self, artifact: XiaohongshuArtifact) -> Path:
        path = self._artifact_dir / artifact.filename
        if not path.is_file():
            raise SocialConnectorUnavailableError(
                "locked Xiaohongshu connector artifact is not installed"
            )
        if self._verify_artifacts and not verify_xiaohongshu_artifact(path, artifact):
            raise SocialConnectorIncompatibleError(
                "locked Xiaohongshu connector artifact checksum does not match"
            )
        return path

    def _selected_artifacts(
        self,
    ) -> tuple[XiaohongshuArtifact, XiaohongshuArtifact]:
        if self._artifacts is not None:
            return self._artifacts
        try:
            return current_xiaohongshu_artifacts()
        except RuntimeError as exc:
            raise SocialConnectorUnavailableError(
                "xiaohongshu-mcp has no artifact for this platform"
            ) from exc

    def _process_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["COOKIES_PATH"] = str(self._cookies_path)
        return env

    def _stop_server(self) -> None:
        if self._server is not None:
            _stop_owned_process(self._server)
        self._clear_server()

    def _clear_server(self) -> None:
        self._server = None
        self._endpoint = None


class XiaohongshuApi(Protocol):
    @property
    def has_login_material(self) -> bool: ...

    def check_login(self) -> bool: ...

    def search_feeds(self, *, keyword: str, filters: Mapping[str, str]) -> Mapping[str, Any]: ...

    def get_feed_detail(self, *, feed_id: str, xsec_token: str) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


class XiaohongshuProcessControl(Protocol):
    @property
    def has_login_material(self) -> bool: ...

    def ensure_started(self) -> str: ...

    def close(self) -> None: ...


class XiaohongshuMcpApi:
    """Only place where fixed internal operations become upstream MCP calls."""

    def __init__(
        self,
        process: XiaohongshuProcessControl | None = None,
        transport: XiaohongshuMcpTransport | None = None,
        *,
        readiness_timeout_seconds: float = 120.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._process = process or XiaohongshuProcess()
        self._transport = transport or OfficialMcpTransport()
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._validated_endpoint: str | None = None

    @property
    def has_login_material(self) -> bool:
        return self._process.has_login_material

    def check_login(self) -> bool:
        result = self._call(_XhsOperation.CHECK_LOGIN_STATUS, {})
        if "未登录" in result:
            return False
        if "已登录" in result:
            return True
        raise SocialConnectorIncompatibleError("Xiaohongshu login result has an unknown shape")

    def search_feeds(self, *, keyword: str, filters: Mapping[str, str]) -> Mapping[str, Any]:
        result = self._call(
            _XhsOperation.SEARCH_FEEDS,
            {"keyword": keyword, "filters": dict(filters)},
        )
        return _json_object(result)

    def get_feed_detail(self, *, feed_id: str, xsec_token: str) -> Mapping[str, Any]:
        result = self._call(
            _XhsOperation.GET_FEED_DETAIL,
            {
                "feed_id": feed_id,
                "xsec_token": xsec_token,
                "load_all_comments": False,
            },
        )
        return _json_object(result)

    def close(self) -> None:
        self._process.close()
        self._validated_endpoint = None

    def _call(self, operation: _XhsOperation, arguments: Mapping[str, Any]) -> str:
        with self._lock:
            endpoint = self._ensure_ready()
            try:
                result = self._transport.call_tool(endpoint, operation.value, arguments)
            except BaseException as exc:
                raise _translate_xhs_exception(
                    exc,
                    has_login_material=self.has_login_material,
                    operation=operation,
                ) from exc
            if result.is_error:
                raise _translate_xhs_call_error(
                    result.text,
                    has_login_material=self.has_login_material,
                    operation=operation,
                )
            if not result.text:
                raise SocialConnectorIncompatibleError("Xiaohongshu MCP returned no textual result")
            return result.text

    def _ensure_ready(self) -> str:
        endpoint = self._process.ensure_started()
        if self._validated_endpoint == endpoint:
            return endpoint
        deadline = self._monotonic() + self._readiness_timeout_seconds
        last_error: BaseException | None = None
        while self._monotonic() < deadline:
            try:
                tools = self._transport.list_tools(endpoint)
                _validate_tools(tools)
            except SocialConnectorIncompatibleError:
                raise
            except BaseException as exc:
                last_error = exc
                self._sleep(0.25)
                continue
            self._validated_endpoint = endpoint
            return endpoint
        raise SocialConnectorUnavailableError(
            "xiaohongshu-mcp did not become ready"
        ) from last_error


class XiaohongshuConnector:
    platform: ClassVar[SocialPlatform] = "xiaohongshu"
    name: ClassVar[str] = "xiaohongshu-mcp"
    version: ClassVar[str] = XIAOHONGSHU_VERSION

    def __init__(
        self,
        api: XiaohongshuApi | None = None,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._api = api or XiaohongshuMcpApi()
        self._today = today

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        if request.platform != self.platform or request.content_types != ("note",):
            raise SocialConnectorIncompatibleError(
                "Xiaohongshu connector only accepts note searches"
            )
        if not self._api.check_login():
            self._raise_auth()
        filters: dict[str, str] = {}
        if request.sort != "relevance":
            filters["sort_by"] = {
                "latest": "最新",
                "popular": "最多点赞",
            }[request.sort]
        applied = ["content_types", "sort"]
        unsupported: list[str] = []
        publish_time, date_applied, date_unsupported = self._date_filter(request)
        if publish_time != "不限":
            filters["publish_time"] = publish_time
        applied.extend(date_applied)
        unsupported.extend(date_unsupported)
        try:
            raw = self._api.search_feeds(keyword=request.query, filters=filters)
        except SocialPlatformError:
            raise
        except BaseException as exc:
            raise _translate_xhs_exception(
                exc, has_login_material=self._api.has_login_material
            ) from exc
        rows = raw.get("feeds", ())
        if not isinstance(rows, list):
            raise SocialConnectorIncompatibleError(
                "Xiaohongshu search response does not contain a feeds list"
            )
        candidates: list[SocialSearchCandidate] = []
        for row in rows[: request.limit]:
            if not isinstance(row, Mapping):
                continue
            candidate = _note_candidate(row)
            if candidate is not None:
                candidates.append(candidate)
        return SocialSearchResponse(
            candidates=tuple(candidates),
            applied_filters=tuple(applied),
            unsupported_filters=tuple(unsupported),
        )

    def resolve_url(self, url: str) -> SocialResolvedUrl | None:
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        except (UnicodeError, ValueError):
            return None
        if parsed.scheme.lower() not in {"http", "https"} or host not in {
            "xiaohongshu.com",
            "www.xiaohongshu.com",
        }:
            return None
        match = re.fullmatch(r"/(?:explore|discovery/item)/([^/]+)", parsed.path.rstrip("/"))
        if match is None:
            return None
        feed_id = match.group(1)
        tokens = parse_qs(parsed.query).get("xsec_token", ())
        token = tokens[0].strip() if tokens else ""
        locator = XiaohongshuNoteLocator(feed_id=feed_id, xsec_token=token) if token else None
        return SocialResolvedUrl(
            platform=self.platform,
            content_type="note",
            canonical_url=_NOTE_URL.format(feed_id=feed_id),
            locator=locator,
        )

    def read(self, locator: SocialResourceLocator) -> SocialReadResponse:
        if not isinstance(locator, XiaohongshuNoteLocator):
            raise SocialConnectorIncompatibleError("unknown Xiaohongshu resource locator")
        if not self._api.check_login():
            self._raise_auth()
        try:
            raw = self._api.get_feed_detail(
                feed_id=locator.feed_id,
                xsec_token=locator.xsec_token,
            )
        except SocialPlatformError:
            raise
        except BaseException as exc:
            raise _translate_xhs_exception(
                exc, has_login_material=self._api.has_login_material
            ) from exc
        outer_id = _text(raw.get("feed_id"))
        if outer_id and outer_id != locator.feed_id:
            raise SocialConnectorIncompatibleError("Xiaohongshu note identity changed")
        data = raw.get("data", raw)
        if not isinstance(data, Mapping):
            raise SocialConnectorIncompatibleError("Xiaohongshu detail has an unknown shape")
        note = data.get("note", data)
        if not isinstance(note, Mapping):
            raise SocialContentNotFoundError("Xiaohongshu note detail is unavailable")
        note_id = _text(note.get("noteId") or note.get("id"))
        if note_id and note_id != locator.feed_id:
            raise SocialConnectorIncompatibleError("Xiaohongshu note identity changed")
        body = _text(note.get("desc"))
        evidence = (SocialEvidence(kind="note_body", text=body),) if body else ()
        unavailable = () if body else ("note_body",)
        user = note.get("user")
        interact = note.get("interactInfo")
        return SocialReadResponse(
            title=_text(note.get("title")),
            author=_user_name(user),
            canonical_url=_NOTE_URL.format(feed_id=locator.feed_id),
            evidence=evidence,
            unavailable_parts=unavailable,
            published_at=_xhs_timestamp(note.get("time")),
            metrics=_xhs_metrics(interact),
        )

    def close(self) -> None:
        self._api.close()

    def _raise_auth(self) -> None:
        if self._api.has_login_material:
            raise SocialAuthExpiredError("Xiaohongshu login has expired")
        raise SocialAuthRequiredError("Xiaohongshu login is required")

    def _date_filter(
        self, request: SocialSearchRequest
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        after = request.published_after
        before = request.published_before
        if after is None and before is None:
            return "不限", (), ()
        today = self._today()
        if after is None or (before is not None and before < today):
            unsupported = tuple(
                name
                for name, value in (
                    ("published_after", after),
                    ("published_before", before),
                )
                if value is not None
            )
            return "不限", (), unsupported
        days = (today - after).days
        if days < 0:
            return "不限", (), ("published_after",)
        if days <= 1:
            value = "一天内"
        elif days <= 7:
            value = "一周内"
        elif days <= 183:
            value = "半年内"
        else:
            return "不限", (), ("published_after",)
        applied = ["published_after"]
        if before is not None:
            applied.append("published_before")
        return value, tuple(applied), ()


def _note_candidate(row: Mapping[str, Any]) -> SocialSearchCandidate | None:
    if row.get("modelType") not in {None, "note"}:
        return None
    feed_id = _text(row.get("id"))
    token = _text(row.get("xsecToken") or row.get("xsec_token"))
    card = row.get("noteCard")
    if not feed_id or not token or not isinstance(card, Mapping):
        return None
    return SocialSearchCandidate(
        locator=XiaohongshuNoteLocator(feed_id=feed_id, xsec_token=token),
        title=_text(card.get("displayTitle") or card.get("title")),
        author=_user_name(card.get("user")),
        canonical_url=_NOTE_URL.format(feed_id=feed_id),
        metrics=_xhs_metrics(card.get("interactInfo")),
    )


def _user_name(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    return _text(value.get("nickname") or value.get("nickName") or value.get("name"))


def _xhs_metrics(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    mapping = {
        "likedCount": "likes",
        "sharedCount": "shares",
        "commentCount": "comments",
        "collectedCount": "collections",
    }
    metrics: dict[str, int] = {}
    for source, target in mapping.items():
        parsed = _count(value.get(source))
        if parsed is not None:
            metrics[target] = parsed
    return metrics


def _count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    match = _COUNT_RE.match(value.strip().replace(",", ""))
    if match is None:
        return None
    multiplier = {None: 1, "万": 10_000, "亿": 100_000_000}[match.group(2)]
    return int(float(match.group(1)) * multiplier)


def _xhs_timestamp(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    timestamp = value / 1000 if value > 10_000_000_000 else value
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _validate_tools(tools: tuple[McpToolDefinition, ...]) -> None:
    by_name = {tool.name: tool for tool in tools}
    expected: dict[_XhsOperation, dict[str, str]] = {
        _XhsOperation.CHECK_LOGIN_STATUS: {},
        _XhsOperation.SEARCH_FEEDS: {"keyword": "string", "filters": "object"},
        _XhsOperation.GET_FEED_DETAIL: {
            "feed_id": "string",
            "xsec_token": "string",
            "load_all_comments": "boolean",
        },
    }
    required: dict[_XhsOperation, set[str]] = {
        _XhsOperation.CHECK_LOGIN_STATUS: set(),
        _XhsOperation.SEARCH_FEEDS: {"keyword"},
        _XhsOperation.GET_FEED_DETAIL: {"feed_id", "xsec_token"},
    }
    for operation, properties in expected.items():
        tool = by_name.get(operation.value)
        if tool is None:
            raise SocialConnectorIncompatibleError(
                "Xiaohongshu MCP is missing a required read-only tool"
            )
        schema = tool.input_schema
        if schema.get("type") != "object":
            raise SocialConnectorIncompatibleError("Xiaohongshu MCP tool schema is incompatible")
        actual_properties = schema.get("properties", {})
        actual_required = schema.get("required", ())
        if (
            not isinstance(actual_properties, Mapping)
            or not isinstance(actual_required, Sequence)
            or isinstance(actual_required, (str, bytes))
        ):
            raise SocialConnectorIncompatibleError("Xiaohongshu MCP tool schema is incompatible")
        if required[operation] != set(actual_required):
            raise SocialConnectorIncompatibleError("Xiaohongshu MCP required arguments changed")
        for property_name, property_type in properties.items():
            definition = actual_properties.get(property_name)
            if not isinstance(definition, Mapping) or definition.get("type") != property_type:
                raise SocialConnectorIncompatibleError("Xiaohongshu MCP argument schema changed")


def _json_object(value: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SocialConnectorIncompatibleError("Xiaohongshu MCP returned invalid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise SocialConnectorIncompatibleError("Xiaohongshu MCP returned non-object JSON")
    return cast(Mapping[str, Any], parsed)


def _translate_xhs_call_error(
    value: str,
    *,
    has_login_material: bool,
    operation: _XhsOperation | None = None,
) -> SocialPlatformError:
    normalized = value.lower()
    if any(marker.lower() in normalized for marker in _ACCESS_MARKERS):
        return SocialAccessRestrictedError("Xiaohongshu restricted the request")
    if any(marker.lower() in normalized for marker in _NOT_FOUND_MARKERS):
        return SocialContentNotFoundError("Xiaohongshu note was not found")
    if any(marker.lower() in normalized for marker in _AUTH_MARKERS):
        if has_login_material:
            return SocialAuthExpiredError("Xiaohongshu login has expired")
        return SocialAuthRequiredError("Xiaohongshu login is required")
    if any(
        marker in normalized
        for marker in (
            "timeout",
            "timed out",
            "deadline exceeded",
            "context canceled",
            "context cancelled",
        )
    ):
        return SocialNetworkError(
            "Xiaohongshu MCP call timed out",
            retryable=True,
            diagnostic_stage=_xhs_diagnostic_stage(normalized, operation),
        )
    return SocialProviderError("Xiaohongshu MCP call failed")


def _translate_xhs_exception(
    exc: BaseException,
    *,
    has_login_material: bool,
    operation: _XhsOperation | None = None,
) -> SocialPlatformError:
    leaves = tuple(_exception_leaves(exc))
    for leaf in leaves:
        if isinstance(leaf, SocialPlatformError):
            return leaf
    for leaf in leaves:
        if isinstance(leaf, httpx.HTTPStatusError):
            status = leaf.response.status_code
            if status in {403, 412}:
                return SocialAccessRestrictedError("Xiaohongshu restricted the request")
            if status == 404:
                return SocialContentNotFoundError("Xiaohongshu note was not found")
            if status >= 500:
                return SocialNetworkError(
                    "Xiaohongshu MCP transport failed",
                    retryable=True,
                    diagnostic_stage=_xhs_diagnostic_stage("", operation),
                )
    for leaf in leaves:
        normalized = f"{type(leaf).__name__} {leaf}".lower()
        if isinstance(leaf, (httpx.ConnectError, httpx.TimeoutException, OSError)) or any(
            marker in normalized for marker in ("timeout", "timed out", "connect")
        ):
            return SocialNetworkError(
                "Xiaohongshu MCP transport failed",
                retryable=True,
                diagnostic_stage=_xhs_diagnostic_stage(normalized, operation),
            )
    combined = " ".join(str(leaf) for leaf in leaves)
    return _translate_xhs_call_error(
        combined,
        has_login_material=has_login_material,
        operation=operation,
    )


def _xhs_diagnostic_stage(value: str, operation: _XhsOperation | None) -> str | None:
    if "search_navigate_failed" in value:
        return "search_navigation"
    if "search_results_wait_failed" in value:
        return "search_results_wait"
    if operation is _XhsOperation.CHECK_LOGIN_STATUS:
        return "login_check"
    if operation is _XhsOperation.SEARCH_FEEDS:
        return "search_request"
    if operation is _XhsOperation.GET_FEED_DETAIL:
        return "detail_request"
    return None


def _exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for nested in exc.exceptions for leaf in _exception_leaves(nested)]
    return [exc]


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _no_window_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _stop_owned_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_async[T](awaitable: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(awaitable)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "XIAOHONGSHU_DEFAULT_MCP_TIMEOUT_SECONDS",
    "XIAOHONGSHU_DEFAULT_SEARCH_MCP_TIMEOUT_SECONDS",
    "McpCallResult",
    "McpToolDefinition",
    "OfficialMcpTransport",
    "XiaohongshuApi",
    "XiaohongshuConnector",
    "XiaohongshuMcpApi",
    "XiaohongshuMcpTransport",
    "XiaohongshuNoteLocator",
    "XiaohongshuProcess",
    "XiaohongshuProcessControl",
]

"""Read-only Bilibili connector backed by ``bilibili-api-python``.

The connector deliberately exposes a smaller contract than the upstream SDK.  It
only maps fixed read-only search, detail, subtitle, article, and opus operations.
"""

from __future__ import annotations

import asyncio
import html
import re
import threading
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, ClassVar, Protocol, cast
from urllib.parse import urlsplit

import httpx
from bilibili_api import Credential

from ..models import (
    SocialAccessRestrictedError,
    SocialAuthExpiredError,
    SocialAuthRequiredError,
    SocialConnectorIncompatibleError,
    SocialContentNotFoundError,
    SocialContentType,
    SocialEvidence,
    SocialNetworkError,
    SocialPlatform,
    SocialPlatformError,
    SocialProviderError,
    SocialRateLimitedError,
    SocialReadResponse,
    SocialResolvedUrl,
    SocialResourceLocator,
    SocialSearchCandidate,
    SocialSearchRequest,
    SocialSearchResponse,
)

_VIDEO_URL = "https://www.bilibili.com/video/{identifier}"
_ARTICLE_URL = "https://www.bilibili.com/read/cv{cvid}"
_OPUS_URL = "https://www.bilibili.com/opus/{opus_id}"
_TAG_RE = re.compile(r"<[^>]*>")
_AUTH_CODES = {-101, -102, -111}
_ACCESS_CODES = {-412, -403, -352, 403, 412}
_NOT_FOUND_CODES = {-404, 62002, 62004}
_RATE_LIMIT_CODES = {-509}


@dataclass(frozen=True)
class BilibiliVideoLocator:
    bvid: str | None
    aid: int | None
    platform: SocialPlatform = field(init=False, default="bilibili")
    content_type: SocialContentType = field(init=False, default="video")

    def __post_init__(self) -> None:
        if not self.bvid and self.aid is None:
            raise ValueError("Bilibili video locator requires a BV or AV id")


@dataclass(frozen=True)
class BilibiliArticleLocator:
    cvid: int
    platform: SocialPlatform = field(init=False, default="bilibili")
    content_type: SocialContentType = field(init=False, default="article")


@dataclass(frozen=True)
class BilibiliOpusLocator:
    opus_id: int
    platform: SocialPlatform = field(init=False, default="bilibili")
    content_type: SocialContentType = field(init=False, default="article")


class BilibiliApi(Protocol):
    """Narrow SDK seam used by the connector and fake tests."""

    @property
    def authenticated(self) -> bool: ...

    def search(
        self,
        *,
        query: str,
        content_type: SocialContentType,
        sort: str,
        published_after: str | None,
        published_before: str | None,
        limit: int,
    ) -> Mapping[str, Any]: ...

    def video_info(self, locator: BilibiliVideoLocator) -> Mapping[str, Any]: ...

    def video_subtitle(
        self, locator: BilibiliVideoLocator, *, cid: int
    ) -> Mapping[str, Any] | None: ...

    def subtitle_body(self, url: str) -> Mapping[str, Any]: ...

    def article_detail(self, locator: BilibiliArticleLocator) -> Mapping[str, Any]: ...

    def opus_detail(self, locator: BilibiliOpusLocator) -> tuple[Mapping[str, Any], str]: ...

    def close(self) -> None: ...


class BilibiliSdkApi:
    """Synchronous, bounded wrapper around the pinned async SDK."""

    def __init__(
        self, *, credential: Credential | None = None, timeout_seconds: float = 15.0
    ) -> None:
        self._credential = credential or Credential()
        self._timeout_seconds = timeout_seconds
        self._credential_lock = threading.RLock()

    @property
    def authenticated(self) -> bool:
        has_sessdata = getattr(self._credential_snapshot(), "has_sessdata", None)
        return bool(callable(has_sessdata) and has_sessdata())

    def set_credential(self, credential: Credential) -> None:
        with self._credential_lock:
            self._credential = credential

    def search(
        self,
        *,
        query: str,
        content_type: SocialContentType,
        sort: str,
        published_after: str | None,
        published_before: str | None,
        limit: int,
    ) -> Mapping[str, Any]:
        from bilibili_api import search

        order_type: Any
        if content_type == "video":
            search_type = search.SearchObjectType.VIDEO
            order_type = {
                "relevance": search.OrderVideo.TOTALRANK,
                "latest": search.OrderVideo.PUBDATE,
                "popular": search.OrderVideo.CLICK,
            }[sort]
        elif content_type == "article":
            search_type = search.SearchObjectType.ARTICLE
            order_type = {
                "relevance": search.OrderArticle.TOTALRANK,
                "latest": search.OrderArticle.PUBDATE,
                "popular": search.OrderArticle.CLICK,
            }[sort]
        else:
            raise SocialConnectorIncompatibleError(
                "Bilibili SDK adapter received an unsupported content type"
            )
        result = _run_async(
            search.search_by_type(
                keyword=query,
                search_type=search_type,
                order_type=order_type,
                time_start=published_after,
                time_end=published_before,
                page=1,
                page_size=limit,
            )
        )
        return _mapping(result)

    def video_info(self, locator: BilibiliVideoLocator) -> Mapping[str, Any]:
        from bilibili_api import video

        resource = video.Video(
            bvid=locator.bvid,
            aid=locator.aid,
            credential=self._credential_snapshot(),
        )
        return _mapping(_run_async(resource.get_info()))

    def video_subtitle(
        self, locator: BilibiliVideoLocator, *, cid: int
    ) -> Mapping[str, Any] | None:
        from bilibili_api import video

        resource = video.Video(
            bvid=locator.bvid,
            aid=locator.aid,
            credential=self._credential_snapshot(),
        )
        result = _run_async(resource.get_subtitle(cid=cid))
        if result is None:
            return None
        return _mapping(result)

    def subtitle_body(self, url: str) -> Mapping[str, Any]:
        normalized = f"https:{url}" if url.startswith("//") else url
        if not normalized.startswith("https://"):
            raise SocialConnectorIncompatibleError("Bilibili returned a non-HTTPS subtitle URL")
        response = httpx.get(normalized, timeout=self._timeout_seconds, follow_redirects=True)
        response.raise_for_status()
        return _mapping(response.json())

    def article_detail(self, locator: BilibiliArticleLocator) -> Mapping[str, Any]:
        from bilibili_api import article

        resource = article.Article(cvid=locator.cvid, credential=self._credential_snapshot())
        return _mapping(_run_async(resource.get_detail()))

    def opus_detail(self, locator: BilibiliOpusLocator) -> tuple[Mapping[str, Any], str]:
        from bilibili_api import opus

        resource = opus.Opus(opus_id=locator.opus_id, credential=self._credential_snapshot())

        async def load() -> tuple[object, object]:
            info = await resource.get_info()  # type: ignore[no-untyped-call]
            markdown = await resource.markdown()
            return info, markdown

        info, markdown = _run_async(load())
        if not isinstance(markdown, str):
            raise SocialConnectorIncompatibleError("Bilibili SDK returned non-text opus content")
        return _mapping(info), markdown

    def close(self) -> None:
        return None

    def _credential_snapshot(self) -> Credential:
        with self._credential_lock:
            return self._credential


class BilibiliConnector:
    platform: ClassVar[SocialPlatform] = "bilibili"
    name: ClassVar[str] = "bilibili-api-python"
    version: ClassVar[str] = "17.4.1"

    def __init__(self, api: BilibiliApi | None = None) -> None:
        self._api = api or BilibiliSdkApi()
        self._lock = threading.Lock()

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        with self._lock:
            return self._search(request)

    def _search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        if request.platform != self.platform:
            raise SocialConnectorIncompatibleError("Bilibili connector received another platform")
        candidates: list[SocialSearchCandidate] = []
        applied = ["content_types", "sort"]
        unsupported: list[str] = []
        after = request.published_after.isoformat() if request.published_after else None
        before = request.published_before.isoformat() if request.published_before else None
        if after is not None and before is not None:
            applied.extend(("published_after", "published_before"))
        else:
            if after is not None:
                unsupported.append("published_after")
            if before is not None:
                unsupported.append("published_before")
            after = None
            before = None

        for content_type in request.content_types:
            if content_type not in {"video", "article"}:
                raise SocialConnectorIncompatibleError(
                    "Bilibili connector received an unsupported content type"
                )
            try:
                raw = self._api.search(
                    query=request.query,
                    content_type=content_type,
                    sort=request.sort,
                    published_after=after,
                    published_before=before,
                    limit=request.limit,
                )
            except SocialPlatformError:
                raise
            except BaseException as exc:
                raise _translate_exception(exc, authenticated=self._api.authenticated) from exc
            rows = raw.get("result", ()) or ()
            if not isinstance(rows, list):
                raise SocialConnectorIncompatibleError(
                    "Bilibili search response does not contain a result list"
                )
            parser = _video_candidate if content_type == "video" else _article_candidate
            for row in rows[: request.limit]:
                if not isinstance(row, Mapping):
                    continue
                candidate = parser(row)
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
            "bilibili.com",
            "m.bilibili.com",
            "www.bilibili.com",
        }:
            return None
        path = parsed.path.rstrip("/")
        video_match = re.fullmatch(r"/video/(BV[0-9A-Za-z]+|av[0-9]+)", path)
        if video_match is not None:
            identifier = video_match.group(1)
            locator = (
                BilibiliVideoLocator(bvid=identifier, aid=None)
                if identifier.startswith("BV")
                else BilibiliVideoLocator(bvid=None, aid=int(identifier[2:]))
            )
            return SocialResolvedUrl(
                platform=self.platform,
                content_type="video",
                canonical_url=_VIDEO_URL.format(identifier=identifier),
                locator=locator,
            )
        article_match = re.fullmatch(r"/read/cv([0-9]+)", path)
        if article_match is not None:
            cvid = int(article_match.group(1))
            return SocialResolvedUrl(
                platform=self.platform,
                content_type="article",
                canonical_url=_ARTICLE_URL.format(cvid=cvid),
                locator=BilibiliArticleLocator(cvid=cvid),
            )
        opus_match = re.fullmatch(r"/opus/([0-9]+)", path)
        if opus_match is not None:
            opus_id = int(opus_match.group(1))
            return SocialResolvedUrl(
                platform=self.platform,
                content_type="article",
                canonical_url=_OPUS_URL.format(opus_id=opus_id),
                locator=BilibiliOpusLocator(opus_id=opus_id),
            )
        return None

    def read(self, locator: SocialResourceLocator) -> SocialReadResponse:
        with self._lock:
            return self._read(locator)

    def _read(self, locator: SocialResourceLocator) -> SocialReadResponse:
        if isinstance(locator, BilibiliVideoLocator):
            return self._read_video(locator)
        if isinstance(locator, BilibiliArticleLocator):
            return self._read_article(locator)
        if isinstance(locator, BilibiliOpusLocator):
            return self._read_opus(locator)
        raise SocialConnectorIncompatibleError("unknown Bilibili resource locator")

    def close(self) -> None:
        self._api.close()

    def _read_video(self, locator: BilibiliVideoLocator) -> SocialReadResponse:
        try:
            info = self._api.video_info(locator)
            actual_bvid = _optional_text(info.get("bvid"))
            if locator.bvid and actual_bvid and locator.bvid != actual_bvid:
                raise SocialConnectorIncompatibleError("Bilibili video identity changed")
            pages = info.get("pages")
            cid = _first_cid(pages)
        except SocialPlatformError:
            raise
        except BaseException as exc:
            raise _translate_exception(exc, authenticated=self._api.authenticated) from exc

        subtitle_text = ""
        unavailable_reasons: dict[str, str] = {}
        try:
            subtitle = self._api.video_subtitle(locator, cid=cid) if cid is not None else None
            subtitle_text = self._read_subtitle_text(subtitle)
        except SocialAuthRequiredError:
            unavailable_reasons["video_subtitle"] = "auth_required"
        except SocialAuthExpiredError:
            unavailable_reasons["video_subtitle"] = "auth_expired"
        except SocialPlatformError:
            raise
        except BaseException as exc:
            translated = _translate_exception(exc, authenticated=self._api.authenticated)
            if isinstance(translated, SocialAuthRequiredError):
                unavailable_reasons["video_subtitle"] = "auth_required"
            elif isinstance(translated, SocialAuthExpiredError):
                unavailable_reasons["video_subtitle"] = "auth_expired"
            else:
                raise translated from exc

        description = _text(info.get("desc"))
        evidence: list[SocialEvidence] = []
        unavailable: list[str] = []
        if description:
            evidence.append(SocialEvidence(kind="video_description", text=description))
        else:
            unavailable.append("video_description")
        if subtitle_text:
            evidence.append(SocialEvidence(kind="video_subtitle", text=subtitle_text))
        else:
            unavailable.append("video_subtitle")
        owner = info.get("owner")
        author = _text(owner.get("name")) if isinstance(owner, Mapping) else ""
        identifier = locator.bvid or f"av{locator.aid}"
        return SocialReadResponse(
            title=_text(info.get("title")),
            author=author,
            canonical_url=_VIDEO_URL.format(identifier=identifier),
            evidence=tuple(evidence),
            unavailable_parts=tuple(unavailable),
            unavailable_reasons=unavailable_reasons,
            published_at=_timestamp(info.get("pubdate")),
            metrics=_integer_metrics(info.get("stat")),
        )

    def _read_article(self, locator: BilibiliArticleLocator) -> SocialReadResponse:
        try:
            raw = self._api.article_detail(locator)
        except SocialPlatformError:
            raise
        except BaseException as exc:
            raise _translate_exception(exc, authenticated=self._api.authenticated) from exc
        detail = raw.get("readInfo", raw)
        if not isinstance(detail, Mapping):
            raise SocialConnectorIncompatibleError("Bilibili article detail has an unknown shape")
        actual_id = _optional_int(detail.get("id")) or _optional_int(detail.get("cvid"))
        if actual_id is not None and actual_id != locator.cvid:
            raise SocialConnectorIncompatibleError("Bilibili article identity changed")
        body = _html_to_text(_text(detail.get("content")))
        evidence = (SocialEvidence(kind="article_body", text=body),) if body else ()
        unavailable = () if body else ("article_body",)
        author_value = detail.get("author")
        author = (
            _text(author_value.get("name"))
            if isinstance(author_value, Mapping)
            else _text(detail.get("author_name") or detail.get("name"))
        )
        return SocialReadResponse(
            title=_strip_markup(detail.get("title")),
            author=author,
            canonical_url=_ARTICLE_URL.format(cvid=locator.cvid),
            evidence=evidence,
            unavailable_parts=unavailable,
            published_at=_timestamp(
                detail.get("publish_time") or detail.get("pub_time") or detail.get("ctime")
            ),
            metrics=_integer_metrics(detail.get("stats") or detail.get("stat")),
        )

    def _read_opus(self, locator: BilibiliOpusLocator) -> SocialReadResponse:
        try:
            raw, markdown = self._api.opus_detail(locator)
        except SocialPlatformError:
            raise
        except BaseException as exc:
            raise _translate_exception(exc, authenticated=self._api.authenticated) from exc
        item = raw.get("item")
        if not isinstance(item, Mapping):
            raise SocialConnectorIncompatibleError("Bilibili opus detail has an unknown shape")
        actual_id = _optional_int(item.get("id_str") or item.get("id"))
        if actual_id is not None and actual_id != locator.opus_id:
            raise SocialConnectorIncompatibleError("Bilibili opus identity changed")
        title, body = _opus_markdown_content(markdown)
        author, published_at = _opus_author(item.get("modules"))
        evidence = (SocialEvidence(kind="article_body", text=body),) if body else ()
        unavailable = () if body else ("article_body",)
        return SocialReadResponse(
            title=title,
            author=author,
            canonical_url=_OPUS_URL.format(opus_id=locator.opus_id),
            evidence=evidence,
            unavailable_parts=unavailable,
            published_at=published_at,
        )

    def _read_subtitle_text(self, subtitle: Mapping[str, Any] | None) -> str:
        if not subtitle:
            return ""
        raw_entries = subtitle.get("subtitles") or subtitle.get("list") or ()
        if not isinstance(raw_entries, list):
            return ""
        entries = [item for item in raw_entries if isinstance(item, Mapping)]
        entries.sort(key=_subtitle_preference)
        for item in entries:
            url = _optional_text(item.get("subtitle_url"))
            if not url:
                continue
            body = self._api.subtitle_body(url).get("body", ())
            if not isinstance(body, list):
                continue
            lines = [
                _text(line.get("content"))
                for line in body
                if isinstance(line, Mapping) and _text(line.get("content"))
            ]
            if lines:
                return "\n".join(lines)
        return ""


def _video_candidate(row: Mapping[str, Any]) -> SocialSearchCandidate | None:
    bvid = _optional_text(row.get("bvid"))
    aid = _optional_int(row.get("aid"))
    if not bvid and aid is None:
        return None
    locator = BilibiliVideoLocator(bvid=bvid, aid=aid)
    identifier = bvid or f"av{aid}"
    return SocialSearchCandidate(
        locator=locator,
        title=_strip_markup(row.get("title")),
        author=_text(row.get("author") or row.get("up_name")),
        canonical_url=_VIDEO_URL.format(identifier=identifier),
        summary=_strip_markup(row.get("description") or row.get("desc")),
        published_at=_timestamp(row.get("pubdate") or row.get("senddate")),
        metrics=_integer_metrics(row),
    )


def _article_candidate(row: Mapping[str, Any]) -> SocialSearchCandidate | None:
    cvid = _optional_int(row.get("id") or row.get("cvid"))
    if cvid is None:
        return None
    return SocialSearchCandidate(
        locator=BilibiliArticleLocator(cvid=cvid),
        title=_strip_markup(row.get("title")),
        author=_text(row.get("name") or row.get("author")),
        canonical_url=_ARTICLE_URL.format(cvid=cvid),
        summary=_strip_markup(row.get("desc") or row.get("description")),
        published_at=_timestamp(row.get("pub_time") or row.get("pubdate")),
        metrics=_integer_metrics(row),
    )


def _opus_markdown_content(value: str) -> tuple[str, str]:
    text = value.strip()
    if text.startswith("---\n"):
        _, separator, remainder = text[4:].partition("\n---\n")
        if separator:
            text = remainder.lstrip()
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    title = ""
    if lines and lines[0].startswith("# "):
        title = lines.pop(0)[2:].strip()
    return title, "\n".join(lines).strip()


def _opus_author(value: object) -> tuple[str, str | None]:
    if not isinstance(value, list):
        return "", None
    for module in value:
        if not isinstance(module, Mapping):
            continue
        author = module.get("module_author")
        if not isinstance(author, Mapping):
            continue
        return _text(author.get("name")), _timestamp(author.get("pub_ts") or author.get("pub_time"))
    return "", None


def _integer_metrics(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    allowed = {"view", "play", "danmaku", "favorites", "favorite", "like", "reply"}
    metrics: dict[str, int] = {}
    for key, item in value.items():
        if (
            isinstance(key, str)
            and key in allowed
            and isinstance(item, int)
            and not isinstance(item, bool)
        ):
            metrics[key] = item
    return metrics


def _subtitle_preference(item: Mapping[str, Any]) -> tuple[int, str]:
    language = f"{_text(item.get('lan'))} {_text(item.get('lan_doc'))}".lower()
    return (0 if "zh" in language or "中" in language else 1, language)


def _first_cid(value: object) -> int | None:
    if not isinstance(value, list) or not value or not isinstance(value[0], Mapping):
        return None
    return _optional_int(value[0].get("cid"))


def _timestamp(value: object) -> str | None:
    timestamp = _optional_int(value)
    if timestamp is None or timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strip_markup(value: object) -> str:
    return html.unescape(_TAG_RE.sub("", _text(value))).strip()


class _ArticleTextParser(HTMLParser):
    _BREAK_TAGS: ClassVar[set[str]] = {
        "br",
        "div",
        "p",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "blockquote",
    }
    _IGNORED_TAGS: ClassVar[set[str]] = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    parser = _ArticleTextParser()
    parser.feed(value)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SocialConnectorIncompatibleError("Bilibili SDK returned a non-object response")
    return cast(Mapping[str, Any], value)


def _run_async[T](awaitable: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(awaitable)


def _translate_exception(exc: BaseException, *, authenticated: bool) -> SocialPlatformError:
    if isinstance(exc, SocialPlatformError):
        return exc
    name = type(exc).__name__
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = getattr(exc, "status", None)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
    if name == "CredentialNoSessdataException":
        return SocialAuthRequiredError("Bilibili session is required")
    if isinstance(code, int):
        if code in _RATE_LIMIT_CODES:
            return SocialRateLimitedError("Bilibili request was rate limited")
        if code in _AUTH_CODES:
            if authenticated:
                return SocialAuthExpiredError("Bilibili session is no longer valid")
            return SocialAuthRequiredError("Bilibili session is required")
        if code in _ACCESS_CODES:
            return SocialAccessRestrictedError("Bilibili rejected the request")
        if code in _NOT_FOUND_CODES:
            return SocialContentNotFoundError("Bilibili content was not found")
    if isinstance(code, int) and code >= 500:
        return SocialNetworkError("Bilibili request failed", retryable=True)
    if name in {"NetworkException", "ConnectError", "ReadTimeout", "ConnectTimeout"} or isinstance(
        exc, (httpx.ConnectError, httpx.TimeoutException)
    ):
        return SocialNetworkError("Bilibili request failed", retryable=True)
    if name == "ArgsException":
        return SocialConnectorIncompatibleError("Bilibili SDK rejected fixed adapter arguments")
    return SocialProviderError("Bilibili SDK request failed")

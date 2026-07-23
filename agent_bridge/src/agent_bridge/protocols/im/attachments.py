"""Download non-voice IM attachments into the canonical attachment store."""

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from agent.attachments import AttachmentError, AttachmentRef, AttachmentStore

from .content import OutboundContent
from .event import InboundEvent
from .inbound_resolver import InboundResolveResult
from .voice import is_voice_attachment

logger = logging.getLogger(__name__)

IM_ATTACHMENT_FAILURE_TEXT = (
    "这条消息里的附件我暂时没处理好，可以重新发送或改发支持的图片、PDF、TXT。"
)


class IMAttachmentResolver:
    """Import supported image/document attachments before conversation routing."""

    def __init__(
        self,
        store: AttachmentStore,
        *,
        timeout: httpx.Timeout | float | None = None,
        max_redirects: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._store = store
        self._timeout = timeout or httpx.Timeout(connect=2.0, read=30.0, write=10.0, pool=2.0)
        self._max_redirects = max_redirects
        self._transport = transport

    async def resolve(self, im_type: str, event: InboundEvent) -> InboundResolveResult:
        del im_type
        candidates = [item for item in _all_attachments(event) if not is_voice_attachment(item)]
        if not candidates:
            return InboundResolveResult.route(event)

        imported: list[AttachmentRef] = []
        try:
            for index, attachment in enumerate(candidates):
                imported.append(await self._import_one(attachment, fallback_index=index))
        except (AttachmentError, httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "IM attachment import failed message_id=%s code=%s",
                event.message_id,
                getattr(exc, "code", type(exc).__name__),
            )
            return InboundResolveResult.direct_reply(
                OutboundContent(
                    chat_id=event.chat_id,
                    chat_scope=event.chat_scope,
                    text=IM_ATTACHMENT_FAILURE_TEXT,
                    reply_to_message_id=event.message_id,
                ),
                reason="attachment_import_failed",
            )
        return InboundResolveResult.route(event, attachments=tuple(imported))

    async def _import_one(self, attachment: object, *, fallback_index: int) -> AttachmentRef:
        url = _attachment_url(attachment)
        if not url:
            raise ValueError("attachment URL missing")
        current_url = url
        redirects = 0
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            while True:
                _validate_download_url(current_url)
                async with client.stream("GET", current_url) as response:
                    if 300 <= response.status_code < 400:
                        if redirects >= self._max_redirects:
                            raise ValueError("too many attachment redirects")
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("attachment redirect missing location")
                        current_url = urljoin(current_url, location)
                        redirects += 1
                        continue
                    response.raise_for_status()
                    name = _display_name(attachment, current_url, fallback_index=fallback_index)
                    declared = _declared_media_type(attachment, response)
                    draft_id, _ = await self._store.import_stream(
                        response.aiter_bytes(),
                        display_name=name,
                        declared_media_type=declared,
                    )
                    return self._store.consume_draft(draft_id)


def _all_attachments(event: InboundEvent) -> list[object]:
    attachments: list[object] = list(event.attachments)
    for element in event.msg_elements:
        attachments.extend(getattr(element, "attachments", []) or [])
    return attachments


def _attachment_url(attachment: object) -> str:
    value = str(
        getattr(attachment, "resolved_url", "") or getattr(attachment, "url", "") or ""
    ).strip()
    return f"https:{value}" if value.startswith("//") else value


def _display_name(attachment: object, url: str, *, fallback_index: int) -> str:
    explicit = str(getattr(attachment, "filename", "") or "").strip()
    if explicit:
        return explicit
    path_name = Path(unquote(urlparse(url).path)).name
    return path_name or f"im-attachment-{fallback_index + 1}"


def _declared_media_type(attachment: object, response: httpx.Response) -> str | None:
    declared = str(getattr(attachment, "content_type", "") or "").split(";", 1)[0].strip()
    if "/" in declared:
        return declared
    response_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    return response_type if "/" in response_type else None


def _validate_download_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("unsafe attachment URL")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("unsafe attachment host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if any(
        (
            ip.is_loopback,
            ip.is_private,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    ):
        raise ValueError("unsafe attachment host")

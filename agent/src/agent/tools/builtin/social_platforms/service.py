"""Connector routing and opaque reference issuance for social platform tools."""

from __future__ import annotations

import threading
from collections.abc import Collection
from dataclasses import dataclass
from typing import cast

from .auth import (
    SocialAuthChallenge,
    SocialAuthInteraction,
    SocialAuthManager,
    SocialAuthStatus,
)
from .auth_requests import (
    SocialAuthRequest,
    SocialAuthRequestStatus,
    SocialAuthRequestStore,
    SocialMissingCapability,
)
from .models import (
    SocialConnectorIncompatibleError,
    SocialConnectorUnavailableError,
    SocialContentType,
    SocialCreatorWorkRelationship,
    SocialCreatorWorksConnector,
    SocialCreatorWorksResponse,
    SocialCreatorWorksScope,
    SocialCreatorWorksSort,
    SocialPlatform,
    SocialPlatformConnector,
    SocialReadResponse,
    SocialResolvedUrl,
    SocialResourceLocator,
    SocialSearchRequest,
    SocialUrlNotAuthorizedError,
    SocialUrlUnsupportedError,
)
from .resources import SocialContinuationEntry, SocialContinuationStore, SocialResourceStore

_MAX_AUTH_CHALLENGE_BYTES = 512 * 1024


class SocialAuthChallengeNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class SocialSearchItem:
    resource_ref: str
    content_type: SocialContentType
    title: str
    author: str
    url: str
    summary: str
    published_at: str | None
    metrics: dict[str, int]
    identity: dict[str, str | int]


@dataclass(frozen=True)
class SocialSearchResult:
    platform: SocialPlatform
    items: tuple[SocialSearchItem, ...]
    applied_filters: tuple[str, ...]
    unsupported_filters: tuple[str, ...]
    connector_name: str
    connector_version: str


@dataclass(frozen=True)
class SocialReadResult:
    platform: SocialPlatform
    content_type: SocialContentType
    response: SocialReadResponse
    connector_name: str
    connector_version: str


@dataclass(frozen=True)
class SocialCreatorWorkItem:
    resource_ref: str
    title: str
    url: str
    published_at: str | None
    metrics: dict[str, int]
    duration_seconds: int | None
    uploader: str
    relationship: SocialCreatorWorkRelationship


@dataclass(frozen=True)
class SocialCreatorWorksResult:
    platform: SocialPlatform
    creator_name: str
    creator_url: str
    sort: str
    page: int
    works: tuple[SocialCreatorWorkItem, ...]
    has_more: bool
    continuation_ref: str | None
    listing_scope: SocialCreatorWorksScope
    connector_name: str
    connector_version: str


class SocialPlatformService:
    def __init__(
        self,
        connectors: tuple[SocialPlatformConnector, ...],
        *,
        resources: SocialResourceStore | None = None,
        continuations: SocialContinuationStore | None = None,
        auth: SocialAuthManager | None = None,
        auth_requests: SocialAuthRequestStore | None = None,
    ) -> None:
        self._connectors: dict[SocialPlatform, SocialPlatformConnector] = {}
        for connector in connectors:
            if connector.platform in self._connectors:
                raise ValueError(f"duplicate social connector: {connector.platform}")
            self._connectors[connector.platform] = connector
        self._resources = resources or SocialResourceStore()
        self._continuations = continuations or SocialContinuationStore()
        self._auth = auth
        self._auth_requests = auth_requests or SocialAuthRequestStore()
        self._auth_challenges: dict[str, SocialAuthChallenge] = {}
        self._auth_challenges_lock = threading.RLock()

    def search(self, *, session_id: str, request: SocialSearchRequest) -> SocialSearchResult:
        connector = self._connector(request.platform)
        response = connector.search(request)
        items: list[SocialSearchItem] = []
        for candidate in response.candidates[:20]:
            locator = candidate.locator
            if (
                locator.platform != request.platform
                or locator.content_type not in request.content_types
            ):
                raise SocialConnectorIncompatibleError(
                    "connector returned a locator outside the requested platform or content types"
                )
            entry = self._resources.put(
                session_id=session_id,
                platform=locator.platform,
                content_type=locator.content_type,
                canonical_url=candidate.canonical_url,
                locator=locator,
                connector_version=connector.version,
            )
            items.append(
                SocialSearchItem(
                    resource_ref=entry.resource_ref,
                    content_type=locator.content_type,
                    title=candidate.title,
                    author=candidate.author,
                    url=candidate.canonical_url,
                    summary=candidate.summary,
                    published_at=candidate.published_at,
                    metrics=dict(candidate.metrics),
                    identity=dict(candidate.identity),
                )
            )
        return SocialSearchResult(
            platform=request.platform,
            items=tuple(items),
            applied_filters=response.applied_filters,
            unsupported_filters=response.unsupported_filters,
            connector_name=connector.name,
            connector_version=connector.version,
        )

    def read(self, *, session_id: str, resource_ref: str) -> SocialReadResult:
        entry = self._resources.get(session_id=session_id, resource_ref=resource_ref)
        connector = self._connector(entry.platform)
        if connector.version != entry.connector_version:
            raise SocialConnectorIncompatibleError("connector version changed after search")
        response = connector.read(entry.locator)
        if response.canonical_url != entry.canonical_url:
            raise SocialConnectorIncompatibleError(
                "connector read result does not match search result"
            )
        return SocialReadResult(
            platform=entry.platform,
            content_type=entry.content_type,
            response=response,
            connector_name=connector.name,
            connector_version=connector.version,
        )

    def creator_works(
        self,
        *,
        session_id: str,
        resource_ref: str | None = None,
        continuation_ref: str | None = None,
        sort: SocialCreatorWorksSort = "popular",
    ) -> SocialCreatorWorksResult:
        if (resource_ref is None) == (continuation_ref is None):
            raise ValueError("creator works target must be exclusive")
        if continuation_ref is not None:
            continuation = self._continuations.get(
                session_id=session_id,
                continuation_ref=continuation_ref,
            )
            return self._creator_works_from_continuation(session_id, continuation)
        assert resource_ref is not None
        entry = self._resources.get(session_id=session_id, resource_ref=resource_ref)
        return self._creator_works_from_resource(
            session_id=session_id,
            platform=entry.platform,
            content_type=entry.content_type,
            canonical_url=entry.canonical_url,
            locator=entry.locator,
            connector_version=entry.connector_version,
            sort=sort,
            page=1,
        )

    def creator_works_url(
        self,
        *,
        session_id: str,
        url: str,
        authorized_urls: tuple[str, ...],
        sort: SocialCreatorWorksSort,
    ) -> SocialCreatorWorksResult:
        resource = self._authorized_url_resource(url, authorized_urls)
        connector = self._connector(resource.platform)
        if resource.locator is None:
            raise SocialUrlUnsupportedError("creator URL lacks a public locator")
        return self._creator_works_from_resource(
            session_id=session_id,
            platform=resource.platform,
            content_type=resource.content_type,
            canonical_url=resource.canonical_url,
            locator=resource.locator,
            connector_version=connector.version,
            sort=sort,
            page=1,
        )

    def _creator_works_from_continuation(
        self,
        session_id: str,
        continuation: SocialContinuationEntry,
    ) -> SocialCreatorWorksResult:
        return self._creator_works_from_resource(
            session_id=session_id,
            platform=continuation.platform,
            content_type=continuation.content_type,
            canonical_url=continuation.canonical_url,
            locator=continuation.locator,
            connector_version=continuation.connector_version,
            sort=cast(SocialCreatorWorksSort, continuation.sort),
            page=continuation.page,
        )

    def _creator_works_from_resource(
        self,
        *,
        session_id: str,
        platform: SocialPlatform,
        content_type: SocialContentType,
        canonical_url: str,
        locator: SocialResourceLocator,
        connector_version: str,
        sort: SocialCreatorWorksSort,
        page: int,
    ) -> SocialCreatorWorksResult:
        if (
            platform != "bilibili"
            or content_type != "creator"
            or sort
            not in {
                "popular",
                "latest",
            }
        ):
            raise SocialConnectorIncompatibleError("creator works request is unsupported")
        connector = self._connector(platform)
        if connector.version != connector_version:
            raise SocialConnectorIncompatibleError("connector version changed after search")
        if not isinstance(connector, SocialCreatorWorksConnector):
            raise SocialConnectorIncompatibleError("connector does not support creator works")
        response: SocialCreatorWorksResponse = connector.creator_works(
            locator,
            sort=sort,
            page=page,
            page_size=20,
        )
        if response.creator_url != canonical_url or response.page != page:
            raise SocialConnectorIncompatibleError("creator works identity changed")
        items: list[SocialCreatorWorkItem] = []
        for work in response.works:
            if work.locator.platform != platform or work.locator.content_type != "video":
                raise SocialConnectorIncompatibleError("creator works returned a foreign resource")
            entry = self._resources.put(
                session_id=session_id,
                platform=platform,
                content_type="video",
                canonical_url=work.canonical_url,
                locator=work.locator,
                connector_version=connector.version,
            )
            items.append(
                SocialCreatorWorkItem(
                    resource_ref=entry.resource_ref,
                    title=work.title,
                    url=work.canonical_url,
                    published_at=work.published_at,
                    metrics=dict(work.metrics),
                    duration_seconds=work.duration_seconds,
                    uploader=work.uploader,
                    relationship=work.relationship,
                )
            )
        next_ref = None
        if response.has_more:
            next_ref = self._continuations.put(
                session_id=session_id,
                platform=platform,
                content_type="creator",
                canonical_url=canonical_url,
                locator=locator,
                connector_version=connector.version,
                sort=sort,
                page=page + 1,
            ).continuation_ref
        return SocialCreatorWorksResult(
            platform=platform,
            creator_name=response.creator_name,
            creator_url=response.creator_url,
            sort=response.sort,
            page=response.page,
            works=tuple(items),
            has_more=response.has_more,
            continuation_ref=next_ref,
            listing_scope=response.listing_scope,
            connector_name=connector.name,
            connector_version=connector.version,
        )

    def direct_url_context(
        self,
        *,
        url: str,
        authorized_urls: tuple[str, ...],
    ) -> tuple[SocialPlatform, SocialContentType]:
        resource = self._authorized_url_resource(url, authorized_urls)
        return resource.platform, resource.content_type

    def read_url(
        self,
        *,
        url: str,
        authorized_urls: tuple[str, ...],
    ) -> SocialReadResult:
        resource = self._authorized_url_resource(url, authorized_urls)
        if resource.locator is None:
            raise SocialUrlUnsupportedError(
                "authorized platform URL lacks connector parameters required for reading"
            )
        connector = self._connector(resource.platform)
        response = connector.read(resource.locator)
        if response.canonical_url != resource.canonical_url:
            raise SocialConnectorIncompatibleError(
                "connector read result does not match authorized platform URL"
            )
        return SocialReadResult(
            platform=resource.platform,
            content_type=resource.content_type,
            response=response,
            connector_name=connector.name,
            connector_version=connector.version,
        )

    def resource_context(
        self,
        *,
        session_id: str,
        resource_ref: str,
    ) -> tuple[SocialPlatform, SocialContentType]:
        entry = self._resources.get(session_id=session_id, resource_ref=resource_ref)
        return entry.platform, entry.content_type

    def close(self) -> None:
        with self._auth_challenges_lock:
            self._auth_challenges.clear()
        self._auth_requests.close()
        if self._auth is not None:
            self._auth.close()
        for connector in self._connectors.values():
            connector.close()

    def auth_status(self, platform: SocialPlatform) -> SocialAuthStatus:
        return self._auth_manager().status(platform)

    def login(
        self,
        platform: SocialPlatform,
        *,
        request_id: str | None = None,
    ) -> SocialAuthStatus:
        interaction = (
            self._auth_interaction(request_id, platform) if request_id is not None else None
        )
        return self._auth_manager().login(platform, interaction=interaction)

    def cancel_login(self, platform: SocialPlatform) -> None:
        self._auth_manager().cancel_login(platform)

    def request_auth(
        self,
        *,
        session_id: str,
        platform: SocialPlatform,
        missing_capabilities: tuple[SocialMissingCapability, ...],
        reason: str,
        origin_surface: str,
        origin_destination_id: str | None,
    ) -> SocialAuthRequest:
        return self._auth_requests.create(
            session_id=session_id,
            platform=platform,
            missing_capabilities=missing_capabilities,
            reason=reason,
            origin_surface=origin_surface,
            origin_destination_id=origin_destination_id,
        )

    def auth_request(self, request_id: str) -> SocialAuthRequest:
        return self._auth_requests.get(request_id)

    def auth_challenge(self, request_id: str) -> SocialAuthChallenge:
        request = self._auth_requests.get(request_id)
        if request.status != "login_in_progress":
            raise SocialAuthChallengeNotFoundError(request_id)
        with self._auth_challenges_lock:
            try:
                return self._auth_challenges[request_id]
            except KeyError as exc:
                raise SocialAuthChallengeNotFoundError(request_id) from exc

    def auth_challenge_kind(self, request_id: str) -> str | None:
        try:
            return self.auth_challenge(request_id).kind
        except SocialAuthChallengeNotFoundError:
            return None

    def auth_requests(
        self,
        *,
        session_id: str | None = None,
        active_only: bool = False,
    ) -> tuple[SocialAuthRequest, ...]:
        return self._auth_requests.list(session_id=session_id, active_only=active_only)

    def transition_auth_request(
        self,
        request_id: str,
        *,
        expected: Collection[SocialAuthRequestStatus],
        status: SocialAuthRequestStatus,
        continuation_run_id: str | None = None,
        error_kind: str | None = None,
    ) -> SocialAuthRequest:
        request = self._auth_requests.transition(
            request_id,
            expected=expected,
            status=status,
            continuation_run_id=continuation_run_id,
            error_kind=error_kind,
        )
        if status != "login_in_progress":
            self._clear_auth_challenge(request_id)
        return request

    def _auth_interaction(
        self,
        request_id: str,
        platform: SocialPlatform,
    ) -> SocialAuthInteraction:
        request = self._auth_requests.get(request_id)
        if request.status != "login_in_progress" or request.platform != platform:
            raise SocialAuthChallengeNotFoundError(request_id)
        return SocialAuthInteraction(
            publish_challenge=lambda challenge: self._publish_auth_challenge(
                request_id,
                challenge,
            ),
            clear_challenge=lambda: self._clear_auth_challenge(request_id),
        )

    def _publish_auth_challenge(
        self,
        request_id: str,
        challenge: SocialAuthChallenge,
    ) -> None:
        if challenge.kind != "qr_image" or challenge.media_type != "image/png":
            raise ValueError("unsupported social auth challenge")
        if not challenge.content or len(challenge.content) > _MAX_AUTH_CHALLENGE_BYTES:
            raise ValueError("social auth challenge has invalid size")
        request = self._auth_requests.get(request_id)
        if request.status != "login_in_progress":
            return
        with self._auth_challenges_lock:
            self._auth_challenges[request_id] = challenge

    def _clear_auth_challenge(self, request_id: str) -> None:
        with self._auth_challenges_lock:
            self._auth_challenges.pop(request_id, None)

    def _connector(self, platform: SocialPlatform) -> SocialPlatformConnector:
        connector = self._connectors.get(platform)
        if connector is None:
            raise SocialConnectorUnavailableError(f"connector is unavailable for {platform}")
        return connector

    def _authorized_url_resource(
        self,
        url: str,
        authorized_urls: tuple[str, ...],
    ) -> SocialResolvedUrl:
        target = self._resolve_url(url)
        assert target is not None
        for authorized_url in authorized_urls:
            authorized = self._resolve_url(authorized_url, required=False)
            if authorized is None:
                continue
            if (
                authorized.platform == target.platform
                and authorized.content_type == target.content_type
                and authorized.canonical_url == target.canonical_url
            ):
                return authorized
        raise SocialUrlNotAuthorizedError("platform URL is not authorized for this turn")

    def _resolve_url(self, url: str, *, required: bool = True) -> SocialResolvedUrl | None:
        for connector in self._connectors.values():
            resolved = connector.resolve_url(url)
            if resolved is None:
                continue
            if resolved.platform != connector.platform:
                raise SocialConnectorIncompatibleError(
                    "connector resolved a URL for another platform"
                )
            if resolved.locator is not None and (
                resolved.locator.platform != resolved.platform
                or resolved.locator.content_type != resolved.content_type
            ):
                raise SocialConnectorIncompatibleError(
                    "connector resolved an inconsistent URL locator"
                )
            return resolved
        if required:
            raise SocialUrlUnsupportedError("platform URL is not supported")
        return None

    def _auth_manager(self) -> SocialAuthManager:
        if self._auth is None:
            raise SocialConnectorUnavailableError("social platform login is unavailable")
        return self._auth


__all__ = [
    "SocialAuthChallengeNotFoundError",
    "SocialCreatorWorksResult",
    "SocialPlatformService",
    "SocialReadResult",
    "SocialSearchItem",
    "SocialSearchResult",
]

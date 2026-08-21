"""Low-frequency GitHub release polling and safe first-party Skill activation."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

import httpx

from ..service import SkillCatalogService
from .models import (
    FirstPartySkillConfig,
    FirstPartySkillState,
    PackageReference,
    ReleaseAsset,
    ReleaseInfo,
    ReleaseManifest,
    ReleasePage,
    SemVer,
    UpdateDiagnostic,
)
from .store import FirstPartySkillStore

logger = logging.getLogger(__name__)


class ReleaseClient(Protocol):
    """Small injectable transport contract for deterministic updater tests."""

    def list_releases(self, *, etag: str | None) -> ReleasePage: ...

    def download_asset(self, asset: ReleaseAsset, *, max_bytes: int) -> bytes: ...

    def close(self) -> None: ...


class GitHubReleaseClient:
    """Bounded unauthenticated client for the one product-pinned public repository."""

    def __init__(self, config: FirstPartySkillConfig) -> None:
        self._config = config
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "agent-friend-first-party-skill-updater/1",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )

    def list_releases(self, *, etag: str | None) -> ReleasePage:
        headers = {"If-None-Match": etag} if etag else None
        response = self._client.get(self._config.releases_url, headers=headers)
        if response.status_code == 304:
            return ReleasePage(not_modified=True, etag=etag)
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, list):
            raise ValueError("GitHub releases response must be an array")
        return ReleasePage(
            not_modified=False,
            etag=response.headers.get("ETag"),
            releases=tuple(ReleaseInfo.from_api(item) for item in raw),
        )

    def download_asset(self, asset: ReleaseAsset, *, max_bytes: int) -> bytes:
        if asset.size > max_bytes:
            raise ValueError(f"release asset {asset.name} exceeds its byte limit")
        chunks: list[bytes] = []
        total = 0
        with self._client.stream(
            "GET",
            asset.api_url,
            headers={"Accept": "application/octet-stream"},
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError as exc:
                    raise ValueError("release asset Content-Length is invalid") from exc
                if declared > max_bytes:
                    raise ValueError(f"release asset {asset.name} exceeds its byte limit")
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"release asset {asset.name} exceeds its byte limit")
                chunks.append(chunk)
        content = b"".join(chunks)
        if len(content) != asset.size:
            raise ValueError(f"release asset {asset.name} size differs from GitHub metadata")
        return content

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True)
class _Candidate:
    release: ReleaseInfo
    manifest: ReleaseManifest
    package_asset: ReleaseAsset
    manifest_digest: str


class FirstPartySkillUpdateService:
    """Poll, verify, cache, and atomically expose one managed first-party Skill."""

    def __init__(
        self,
        *,
        store: FirstPartySkillStore,
        catalog: SkillCatalogService,
        product_version: str | None,
        config: FirstPartySkillConfig | None = None,
        client_factory: Callable[[], ReleaseClient] | None = None,
    ) -> None:
        self.config = config or store.config
        self.store = store
        self.catalog = catalog
        self._client_factory = client_factory or (lambda: GitHubReleaseClient(self.config))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.RLock()
        self._client_lock = threading.RLock()
        self._active_client: ReleaseClient | None = None
        try:
            self._host_version = SemVer.parse(product_version) if product_version else None
        except ValueError:
            self._host_version = None
            initial_result = "host_version_invalid"
        else:
            initial_result = (
                "not_checked" if self._host_version is not None else "host_version_unavailable"
            )
        self._runtime_result = initial_result
        self._runtime_detail: str | None = None

    @property
    def enabled(self) -> bool:
        return self._host_version is not None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="FirstPartySkillUpdater",
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        thread = self._thread
        self._thread = None
        self._stop_event.set()
        with self._client_lock:
            active_client = self._active_client
        if active_client is not None:
            active_client.close()
        if thread is not None:
            thread.join(timeout=timeout)

    def check_once(self) -> UpdateDiagnostic:
        if self._host_version is None:
            return self.diagnostic()
        checked_at = datetime.now(UTC).isoformat()
        _root, source, _reference, state = self.store.resolve_effective()
        request_etag = (
            state.etag
            if state.last_checked_product_version == str(self._host_version)
            and (state.active is None or source == "managed")
            else None
        )
        client = self._client_factory()
        with self._client_lock:
            self._active_client = client
        try:
            page = client.list_releases(etag=request_etag)
            if page.not_modified:
                self._record_result(
                    checked_at=checked_at,
                    result="not_modified",
                    detail=None,
                    etag=state.etag,
                )
                return self.diagnostic()
            candidates, invalid_count = self._load_candidates(client, page.releases)
            if not candidates:
                result = "no_releases" if not page.releases else "release_validation_failed"
                detail = None if not invalid_count else f"invalid_releases={invalid_count}"
                self._record_result(
                    checked_at=checked_at,
                    result=result,
                    detail=detail,
                    etag=page.etag if not page.releases else None,
                )
                return self.diagnostic()
            by_sequence: dict[int, _Candidate] = {}
            for candidate in candidates:
                existing = by_sequence.get(candidate.manifest.release_sequence)
                if existing is not None and (
                    existing.release.release_id != candidate.release.release_id
                    or existing.manifest_digest != candidate.manifest_digest
                ):
                    raise ValueError("multiple immutable releases claim the same sequence")
                by_sequence[candidate.manifest.release_sequence] = candidate
            ordered = sorted(by_sequence.values(), key=lambda item: item.manifest.release_sequence)
            observed = ordered[-1]
            compatible = [
                candidate
                for candidate in ordered
                if candidate.manifest.compatible_with(self._host_version)
            ]
            if not compatible:
                self._commit_observation(
                    state=state,
                    observed=observed,
                    checked_at=checked_at,
                    result="no_compatible_release",
                    detail=f"host_version={self._host_version}",
                    etag=page.etag,
                )
                return self.diagnostic()
            selected = compatible[-1]
            self._apply_candidate(
                client,
                selected=selected,
                observed=observed,
                checked_at=checked_at,
                etag=page.etag,
            )
        except Exception as exc:
            logger.warning("first-party Skill update check failed", exc_info=True)
            self._record_result(
                checked_at=checked_at,
                result="check_failed",
                detail=f"{type(exc).__name__}: {exc}",
                etag=None,
            )
        finally:
            with self._client_lock:
                if self._active_client is client:
                    self._active_client = None
            client.close()
        return self.diagnostic()

    def diagnostic(self) -> UpdateDiagnostic:
        root, source, reference, state = self.store.resolve_effective()
        del root
        with self._status_lock:
            runtime_result = self._runtime_result
            runtime_detail = self._runtime_detail
        result = state.last_result
        detail = state.last_detail
        if not self.enabled and runtime_result != "not_checked":
            result = runtime_result
            detail = runtime_detail
        return UpdateDiagnostic(
            enabled=self.enabled,
            source=source,
            active_sequence=(reference.release_sequence if reference is not None else None),
            active_package_sha256=(reference.package_sha256 if reference is not None else None),
            highest_seen_sequence=state.highest_seen_sequence,
            last_checked_at=state.last_checked_at,
            last_result=result,
            last_detail=detail,
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.check_once()
            if self._stop_event.wait(self.config.interval_seconds):
                return

    def _load_candidates(
        self,
        client: ReleaseClient,
        releases: tuple[ReleaseInfo, ...],
    ) -> tuple[list[_Candidate], int]:
        candidates: list[_Candidate] = []
        invalid = 0
        for release in releases:
            if release.draft or release.prerelease or not release.immutable:
                continue
            if self.config.sequence_from_tag(release.tag) is None:
                continue
            manifest_asset = release.asset(self.config.manifest_asset_name)
            package_asset = release.asset(self.config.package_asset_name)
            if manifest_asset is None or package_asset is None:
                invalid += 1
                continue
            try:
                manifest_bytes = client.download_asset(
                    manifest_asset,
                    max_bytes=self.config.max_manifest_bytes,
                )
                manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
                if manifest_digest != manifest_asset.digest:
                    raise ValueError("manifest asset digest differs from GitHub metadata")
                manifest = ReleaseManifest.from_json(json.loads(manifest_bytes.decode("utf-8")))
                manifest.validate_release(release, self.config)
                if manifest.package_sha256 != package_asset.digest:
                    raise ValueError("package digest differs between manifest and GitHub metadata")
                if manifest.package_size != package_asset.size:
                    raise ValueError("package size differs between manifest and GitHub metadata")
                if package_asset.size > self.config.max_package_bytes:
                    raise ValueError("package asset exceeds the configured byte limit")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, httpx.HTTPError):
                invalid += 1
                continue
            candidates.append(
                _Candidate(
                    release=release,
                    manifest=manifest,
                    package_asset=package_asset,
                    manifest_digest=manifest_digest,
                )
            )
        return candidates, invalid

    def _apply_candidate(
        self,
        client: ReleaseClient,
        *,
        selected: _Candidate,
        observed: _Candidate,
        checked_at: str,
        etag: str | None,
    ) -> None:
        reference = PackageReference.from_manifest(
            selected.manifest,
            release_id=selected.release.release_id,
        )
        with self.store.locked():
            current = self.store.load_state()
            replay_reason = self._replay_reason(current, observed)
            if replay_reason is not None:
                self.store.save_state(
                    current.with_result(
                        checked_at=checked_at,
                        result="replay_ignored",
                        detail=replay_reason,
                        product_version=str(self._host_version),
                    )
                )
                self._set_runtime_result("replay_ignored", replay_reason)
                return
            if (
                current.active is not None
                and reference.release_sequence <= current.active.release_sequence
            ):
                unchanged = self._observed_state(
                    current,
                    observed,
                    checked_at=checked_at,
                    result="no_update",
                    detail=None,
                    etag=etag,
                )
                self.store.save_state(unchanged)
                self._set_runtime_result("no_update", None)
                return
            if not self.store.cached_package_valid(reference):
                archive = client.download_asset(
                    selected.package_asset,
                    max_bytes=self.config.max_package_bytes,
                )
                if hashlib.sha256(archive).hexdigest() != selected.package_asset.digest:
                    raise ValueError("downloaded package digest differs from GitHub metadata")
                self.store.install_archive(archive, reference)
            old_state = current
            updated = self._observed_state(
                replace(current, previous=current.active, active=reference),
                observed,
                checked_at=checked_at,
                result="updated",
                detail=f"release={reference.release_tag}",
                etag=etag,
            )
            self.store.save_state(updated)

        old_digest = old_state.active.package_sha256 if old_state.active is not None else None
        if old_digest == reference.package_sha256:
            self._set_runtime_result("updated", f"release={reference.release_tag}")
            return
        try:
            snapshot = self.catalog.refresh()
            guide = snapshot.by_id().get(f"builtin/{self.config.skill_name}")
            expected_root = self.store.package_root(reference).resolve(strict=True)
            if guide is None or guide.package_root != expected_root:
                raise RuntimeError("catalog did not activate the committed first-party package")
        except Exception:
            with self.store.locked():
                if self.store.load_state() == updated:
                    self.store.save_state(
                        old_state.with_result(
                            checked_at=checked_at,
                            result="switch_failed",
                            detail=f"release={reference.release_tag}",
                            product_version=str(self._host_version),
                        )
                    )
            self.catalog.refresh()
            raise
        self._set_runtime_result("updated", f"release={reference.release_tag}")

    def _commit_observation(
        self,
        *,
        state: FirstPartySkillState,
        observed: _Candidate,
        checked_at: str,
        result: str,
        detail: str | None,
        etag: str | None,
    ) -> None:
        del state
        with self.store.locked():
            current = self.store.load_state()
            replay_reason = self._replay_reason(current, observed)
            final_result: str
            final_detail: str | None
            if replay_reason is not None:
                updated = current.with_result(
                    checked_at=checked_at,
                    result="replay_ignored",
                    detail=replay_reason,
                    product_version=str(self._host_version),
                )
                final_result = "replay_ignored"
                final_detail = replay_reason
            else:
                updated = self._observed_state(
                    current,
                    observed,
                    checked_at=checked_at,
                    result=result,
                    detail=detail,
                    etag=etag,
                )
                final_result = result
                final_detail = detail
            self.store.save_state(updated)
        self._set_runtime_result(final_result, final_detail)

    @staticmethod
    def _replay_reason(state: FirstPartySkillState, observed: _Candidate) -> str | None:
        sequence = observed.manifest.release_sequence
        if sequence < state.highest_seen_sequence:
            return f"observed_sequence={sequence} below highest_seen={state.highest_seen_sequence}"
        if (
            sequence == state.highest_seen_sequence
            and state.highest_seen_sequence > 0
            and (
                state.highest_seen_release_id != observed.release.release_id
                or state.highest_seen_tag != observed.release.tag
            )
        ):
            return "highest seen sequence has a different immutable release identity"
        return None

    def _observed_state(
        self,
        state: FirstPartySkillState,
        observed: _Candidate,
        *,
        checked_at: str,
        result: str,
        detail: str | None,
        etag: str | None,
    ) -> FirstPartySkillState:
        sequence = observed.manifest.release_sequence
        if sequence >= state.highest_seen_sequence:
            state = replace(
                state,
                highest_seen_sequence=sequence,
                highest_seen_release_id=observed.release.release_id,
                highest_seen_tag=observed.release.tag,
            )
        return state.with_result(
            checked_at=checked_at,
            result=result,
            detail=detail,
            etag=etag,
            product_version=str(self._host_version),
        )

    def _record_result(
        self,
        *,
        checked_at: str,
        result: str,
        detail: str | None,
        etag: str | None,
    ) -> None:
        with self.store.locked():
            state = self.store.load_state()
            self.store.save_state(
                state.with_result(
                    checked_at=checked_at,
                    result=result,
                    detail=detail,
                    etag=etag,
                    product_version=str(self._host_version),
                )
            )
        self._set_runtime_result(result, detail)

    def _set_runtime_result(self, result: str, detail: str | None) -> None:
        with self._status_lock:
            self._runtime_result = result
            self._runtime_detail = detail

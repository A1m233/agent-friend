from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from agent.skills import SkillCatalogService, SkillSelection
from agent.skills.first_party_updates import (
    FirstPartySkillConfig,
    FirstPartySkillStore,
    FirstPartySkillUpdateService,
    PackageReference,
    ReleaseAsset,
    ReleaseInfo,
    ReleaseManifest,
    ReleasePage,
)
from agent.skills.first_party_updates.store import package_snapshot_digest


class _FakeReleaseClient:
    def __init__(
        self,
        page: ReleasePage,
        assets: dict[str, bytes],
        *,
        asset_errors: dict[str, Exception] | None = None,
        list_error: BaseException | None = None,
    ) -> None:
        self.page = page
        self.assets = assets
        self.asset_errors = asset_errors or {}
        self.list_error = list_error
        self.etags: list[str | None] = []
        self.downloads: list[str] = []
        self.download_urls: list[str] = []
        self.closed = False

    def list_releases(self, *, etag: str | None) -> ReleasePage:
        self.etags.append(etag)
        if self.list_error is not None:
            raise self.list_error
        return self.page

    def download_asset(self, asset: ReleaseAsset, *, max_bytes: int) -> bytes:
        self.downloads.append(asset.name)
        self.download_urls.append(asset.api_url)
        if error := self.asset_errors.get(asset.api_url):
            raise error
        value = self.assets[asset.api_url]
        if len(value) > max_bytes:
            raise ValueError("fake asset exceeds max_bytes")
        return value

    def close(self) -> None:
        self.closed = True


def _write_package(root: Path, body: str, *, sentinel: Path) -> None:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f"---\nname: agent-friend-guide\ndescription: Managed guide test\n---\n\n{body}\n",
        encoding="utf-8",
    )
    references = root / "references"
    references.mkdir()
    (references / "dynamic.md").write_text(f"reference {body}\n", encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "must-not-run.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    assets = root / "assets"
    assets.mkdir()
    (assets / "payload.bin").write_bytes(b"\x00\x01managed-resource")
    metadata = root / "metadata"
    metadata.mkdir()
    content_digest = package_snapshot_digest(root, exclude_source_metadata=True)
    (metadata / "source.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product_repository": "https://github.com/A1m233/agent-friend",
                "reviewed_product_commit": "a" * 40,
                "reviewed_at": "2026-08-21",
                "product_version": "0.2.1",
                "content_sha256": content_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _archive(root: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(info, path.read_bytes())
    return output.getvalue()


def _release(
    *,
    sequence: int,
    archive: bytes,
    snapshot_sha256: str,
    source_commit: str,
    minimum: str = "0.2.0",
    maximum: str = "0.3.0",
) -> tuple[ReleaseInfo, dict[str, bytes]]:
    config = FirstPartySkillConfig()
    tag = f"agent-friend-guide-r{sequence}"
    manifest = {
        "schema_version": 1,
        "skill_name": config.skill_name,
        "release_sequence": sequence,
        "release_tag": tag,
        "source_commit": source_commit,
        "package_sha256": hashlib.sha256(archive).hexdigest(),
        "package_snapshot_sha256": snapshot_sha256,
        "package_size": len(archive),
        "min_product_version": minimum,
        "max_product_version_exclusive": maximum,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    manifest_asset = ReleaseAsset(
        asset_id=sequence * 10 + 1,
        name=config.manifest_asset_name,
        api_url=f"memory://manifest/{sequence}",
        size=len(manifest_bytes),
        digest=hashlib.sha256(manifest_bytes).hexdigest(),
    )
    package_asset = ReleaseAsset(
        asset_id=sequence * 10 + 2,
        name=config.package_asset_name,
        api_url=f"memory://package/{sequence}",
        size=len(archive),
        digest=hashlib.sha256(archive).hexdigest(),
    )
    release = ReleaseInfo(
        release_id=1_000 + sequence,
        tag=tag,
        target_commitish=source_commit,
        immutable=True,
        draft=False,
        prerelease=False,
        assets=(manifest_asset, package_asset),
    )
    return release, {
        manifest_asset.api_url: manifest_bytes,
        package_asset.api_url: archive,
    }


def _runtime(
    tmp_path: Path,
) -> tuple[FirstPartySkillStore, SkillCatalogService]:
    personal = tmp_path / "skills"
    store = FirstPartySkillStore(personal)

    def overrides() -> dict[str, Path | None]:
        root, _source, _reference, _state = store.resolve_effective()
        return {store.config.skill_name: root}

    catalog = SkillCatalogService(
        personal_root=personal,
        shared_root=tmp_path / "shared",
        builtin_override_provider=overrides,
        watcher_enabled=False,
    )
    catalog.initialize()
    return store, catalog


def _release_batch(
    tmp_path: Path,
    sequences: tuple[int, ...],
    *,
    incompatible: tuple[int, ...] = (),
) -> tuple[tuple[ReleaseInfo, ...], dict[str, bytes]]:
    source = tmp_path / "batch-source"
    _write_package(source, "REMOTE GUIDE", sentinel=tmp_path / "script-ran")
    archive = _archive(source)
    snapshot = package_snapshot_digest(source)
    releases: list[ReleaseInfo] = []
    all_assets: dict[str, bytes] = {}
    for sequence in sequences:
        release, assets = _release(
            sequence=sequence,
            archive=archive,
            snapshot_sha256=snapshot,
            source_commit=f"{sequence:040x}",
            minimum="1.0.0" if sequence in incompatible else "0.2.0",
            maximum="2.0.0" if sequence in incompatible else "0.3.0",
        )
        releases.append(release)
        all_assets.update(assets)
    return tuple(releases), all_assets


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/repos/example/releases/assets/1")
    return httpx.HTTPStatusError(
        f"HTTP {status}",
        request=request,
        response=httpx.Response(status, request=request),
    )


def test_full_package_update_switches_next_turn_without_executing_script(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "script-ran"
    source = tmp_path / "source"
    _write_package(source, "REMOTE GUIDE BODY", sentinel=sentinel)
    archive = _archive(source)
    release, assets = _release(
        sequence=1,
        archive=archive,
        snapshot_sha256=package_snapshot_digest(source),
        source_commit="b" * 40,
    )
    store, catalog = _runtime(tmp_path)
    baseline = catalog.snapshot.by_id()["builtin/agent-friend-guide"]
    old_turn = catalog.begin_turn(
        (SkillSelection(baseline.id, baseline.version),),
        model_window=100_000,
    )
    client = _FakeReleaseClient(
        ReleasePage(not_modified=False, etag='"release-1"', releases=(release,)),
        assets,
    )
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    diagnostic = updater.check_once()
    current = catalog.snapshot.by_id()["builtin/agent-friend-guide"]
    new_turn = catalog.begin_turn(
        (SkillSelection(current.id, current.version),),
        model_window=100_000,
    )

    assert diagnostic.last_result == "updated"
    assert diagnostic.active_sequence == 1
    assert current.id == baseline.id
    assert current.package_root != baseline.package_root
    assert "REMOTE GUIDE BODY" not in old_turn.activations[0].contents
    assert "REMOTE GUIDE BODY" in new_turn.activations[0].contents
    assert (current.package_root / "references" / "dynamic.md").is_file()
    assert (current.package_root / "scripts" / "must-not-run.py").is_file()
    assert (current.package_root / "assets" / "payload.bin").is_file()
    assert not sentinel.exists()
    assert client.downloads == [
        "agent-friend-guide.manifest.json",
        "agent-friend-guide.zip",
    ]
    assert client.closed


def test_tampered_package_keeps_packaged_baseline(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_package(source, "TRUSTED BODY", sentinel=tmp_path / "script-ran")
    archive = _archive(source)
    release, assets = _release(
        sequence=1,
        archive=archive,
        snapshot_sha256=package_snapshot_digest(source),
        source_commit="c" * 40,
    )
    assets["memory://package/1"] = archive + b"tampered"
    store, catalog = _runtime(tmp_path)
    baseline = catalog.snapshot.by_id()["builtin/agent-friend-guide"]
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: _FakeReleaseClient(
            ReleasePage(not_modified=False, etag='"tampered"', releases=(release,)),
            assets,
        ),
    )

    diagnostic = updater.check_once()

    assert diagnostic.last_result == "check_failed"
    assert diagnostic.source == "baseline"
    assert diagnostic.highest_seen_sequence == 0
    assert (
        catalog.snapshot.by_id()["builtin/agent-friend-guide"].package_root == baseline.package_root
    )


def test_higher_sequence_can_roll_back_content_and_old_response_is_ignored(
    tmp_path: Path,
) -> None:
    source_a = tmp_path / "source-a"
    source_b = tmp_path / "source-b"
    _write_package(source_a, "GOOD VERSION A", sentinel=tmp_path / "script-a")
    _write_package(source_b, "BAD VERSION B", sentinel=tmp_path / "script-b")
    archive_a = _archive(source_a)
    archive_b = _archive(source_b)
    release_1, assets_1 = _release(
        sequence=1,
        archive=archive_a,
        snapshot_sha256=package_snapshot_digest(source_a),
        source_commit="d" * 40,
    )
    release_2, assets_2 = _release(
        sequence=2,
        archive=archive_b,
        snapshot_sha256=package_snapshot_digest(source_b),
        source_commit="e" * 40,
    )
    release_3, assets_3 = _release(
        sequence=3,
        archive=archive_a,
        snapshot_sha256=package_snapshot_digest(source_a),
        source_commit="f" * 40,
    )
    store, catalog = _runtime(tmp_path)

    for release, assets in (
        (release_1, assets_1),
        (release_2, assets_2),
        (release_3, assets_3),
    ):

        def client_factory(
            release: ReleaseInfo = release,
            assets: dict[str, bytes] = assets,
        ) -> _FakeReleaseClient:
            return _FakeReleaseClient(
                ReleasePage(not_modified=False, etag=None, releases=(release,)),
                assets,
            )

        updater = FirstPartySkillUpdateService(
            store=store,
            catalog=catalog,
            product_version="0.2.1",
            client_factory=client_factory,
        )
        assert updater.check_once().last_result == "updated"

    active = catalog.snapshot.by_id()["builtin/agent-friend-guide"]
    assert "GOOD VERSION A" in active.canonical_entry_path.read_text(encoding="utf-8")
    state = store.load_state()
    assert state.active is not None
    assert state.active.release_sequence == 3

    stale_updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: _FakeReleaseClient(
            ReleasePage(not_modified=False, etag=None, releases=(release_2,)),
            assets_2,
        ),
    )
    diagnostic = stale_updater.check_once()

    assert diagnostic.last_result == "replay_ignored"
    assert diagnostic.active_sequence == 3
    assert (
        catalog.snapshot.by_id()["builtin/agent-friend-guide"].package_root == active.package_root
    )


@pytest.mark.parametrize("unsafe_name", ["../outside.txt", "payload.txt:stream", "CON.txt"])
def test_store_rejects_unsafe_archive_paths(tmp_path: Path, unsafe_name: str) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr(unsafe_name, b"escape")
    archive = output.getvalue()
    reference = PackageReference.from_manifest(
        ReleaseManifest.from_json(
            {
                "schema_version": 1,
                "skill_name": "agent-friend-guide",
                "release_sequence": 1,
                "release_tag": "agent-friend-guide-r1",
                "source_commit": "a" * 40,
                "package_sha256": hashlib.sha256(archive).hexdigest(),
                "package_snapshot_sha256": "0" * 64,
                "package_size": len(archive),
                "min_product_version": "0.2.0",
                "max_product_version_exclusive": "0.3.0",
            }
        ),
        release_id=1,
    )
    store = FirstPartySkillStore(tmp_path / "skills")

    with pytest.raises(ValueError, match=r"unsafe path|Windows-ambiguous path"):
        store.install_archive(archive, reference)

    assert not (tmp_path / "outside.txt").exists()


def test_corrupt_state_does_not_promote_orphan_cache(tmp_path: Path) -> None:
    store, catalog = _runtime(tmp_path)
    baseline = catalog.snapshot.by_id()["builtin/agent-friend-guide"]
    orphan = store.versions_root / ("a" * 64) / "agent-friend-guide"
    orphan.mkdir(parents=True)
    (orphan / "SKILL.md").write_text("uncommitted", encoding="utf-8")
    store.root.mkdir(parents=True, exist_ok=True)
    store.state_path.write_text("{broken", encoding="utf-8")

    catalog.refresh()
    root, source, reference, state = store.resolve_effective()

    assert root is None
    assert source == "baseline"
    assert reference is None
    assert state.last_result == "state_corrupt"
    assert (
        catalog.snapshot.by_id()["builtin/agent-friend-guide"].package_root == baseline.package_root
    )


def test_semantically_inconsistent_state_falls_back_to_baseline(tmp_path: Path) -> None:
    store, catalog = _runtime(tmp_path)
    baseline = catalog.snapshot.by_id()["builtin/agent-friend-guide"]
    store.root.mkdir(parents=True, exist_ok=True)
    store.state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "highest_seen_sequence": 2,
                "highest_seen_release_id": None,
                "highest_seen_tag": None,
                "active": None,
                "previous": None,
                "etag": None,
                "last_checked_product_version": "0.2.1",
                "last_checked_at": "2026-08-21T00:00:00+00:00",
                "last_result": "updated",
                "last_detail": None,
            }
        ),
        encoding="utf-8",
    )

    root, source, reference, state = store.resolve_effective()
    catalog.refresh()

    assert root is None
    assert source == "baseline"
    assert reference is None
    assert state.last_result == "state_corrupt"
    assert (
        catalog.snapshot.by_id()["builtin/agent-friend-guide"].package_root == baseline.package_root
    )


def test_incompatible_release_is_recorded_without_downloading_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_package(source, "FUTURE BODY", sentinel=tmp_path / "script")
    archive = _archive(source)
    release, assets = _release(
        sequence=7,
        archive=archive,
        snapshot_sha256=package_snapshot_digest(source),
        source_commit="9" * 40,
        minimum="1.0.0",
        maximum="2.0.0",
    )
    client = _FakeReleaseClient(
        ReleasePage(not_modified=False, etag='"future"', releases=(release,)),
        assets,
    )
    store, catalog = _runtime(tmp_path)
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    diagnostic = updater.check_once()

    assert diagnostic.last_result == "no_compatible_release"
    assert diagnostic.highest_seen_sequence == 7
    assert diagnostic.source == "baseline"
    assert client.downloads == ["agent-friend-guide.manifest.json"]

    upgraded_client = _FakeReleaseClient(
        ReleasePage(not_modified=False, etag='"future"', releases=(release,)),
        assets,
    )
    upgraded = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="1.0.0",
        client_factory=lambda: upgraded_client,
    )

    assert upgraded.check_once().last_result == "updated"
    assert upgraded_client.etags == [None]


def test_many_compatible_releases_only_download_latest_manifest_and_package(tmp_path: Path) -> None:
    sequences = tuple(range(1, 31, 2)) + tuple(range(30, 0, -2))
    releases, assets = _release_batch(tmp_path, sequences)
    client = _FakeReleaseClient(ReleasePage(False, '"many"', releases), assets)
    store, catalog = _runtime(tmp_path)
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    diagnostic = updater.check_once()

    assert diagnostic.last_result == "updated"
    assert diagnostic.active_sequence == diagnostic.highest_seen_sequence == 30
    assert len(client.etags) + len(client.download_urls) == 3
    assert client.download_urls == ["memory://manifest/30", "memory://package/30"]


def test_highest_compatible_release_preserves_higher_incompatible_observation(
    tmp_path: Path,
) -> None:
    releases, assets = _release_batch(tmp_path, (1, 3, 2), incompatible=(3,))
    client = _FakeReleaseClient(ReleasePage(False, '"mixed"', releases), assets)
    store, catalog = _runtime(tmp_path)
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    diagnostic = updater.check_once()

    assert diagnostic.last_result == "updated"
    assert diagnostic.active_sequence == 2
    assert diagnostic.highest_seen_sequence == 3
    assert client.download_urls == [
        "memory://manifest/3",
        "memory://manifest/2",
        "memory://package/2",
    ]


def test_invalid_newest_manifest_falls_back_without_promoting_its_sequence(tmp_path: Path) -> None:
    releases, assets = _release_batch(tmp_path, (1, 3, 2))
    assets["memory://manifest/3"] += b"tampered"
    client = _FakeReleaseClient(ReleasePage(False, '"invalid-newest"', releases), assets)
    store, catalog = _runtime(tmp_path)
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    diagnostic = updater.check_once()

    assert diagnostic.last_result == "updated"
    assert diagnostic.active_sequence == diagnostic.highest_seen_sequence == 2
    assert client.download_urls == [
        "memory://manifest/3",
        "memory://manifest/2",
        "memory://package/2",
    ]


@pytest.mark.parametrize("conflict", ["release_id", "manifest_digest"])
def test_duplicate_old_sequence_is_rejected_before_compatible_early_exit(
    tmp_path: Path, conflict: str
) -> None:
    releases, assets = _release_batch(tmp_path, (3, 1))
    older = releases[-1]
    if conflict == "release_id":
        duplicate = replace(older, release_id=older.release_id + 10_000)
    else:
        duplicate = replace(
            older,
            assets=(replace(older.assets[0], digest="0" * 64), older.assets[1]),
        )
    client = _FakeReleaseClient(ReleasePage(False, '"conflict"', (*releases, duplicate)), assets)
    store, catalog = _runtime(tmp_path)
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    diagnostic = updater.check_once()

    assert diagnostic.last_result == "check_failed"
    assert diagnostic.active_sequence is None
    assert diagnostic.highest_seen_sequence == 0
    assert "same sequence" in (diagnostic.last_detail or "")
    assert not client.download_urls


@pytest.mark.parametrize("failure", [403, 429, 500, "connection"])
def test_manifest_transport_failure_stops_check_without_installing_older_release(
    tmp_path: Path, failure: int | str
) -> None:
    releases, assets = _release_batch(tmp_path, (1, 3, 2))
    error = (
        _http_status_error(failure)
        if isinstance(failure, int)
        else httpx.ConnectError("connection closed")
    )
    client = _FakeReleaseClient(
        ReleasePage(False, '"unavailable"', releases),
        assets,
        asset_errors={"memory://manifest/3": error},
    )
    store, catalog = _runtime(tmp_path)
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    diagnostic = updater.check_once()

    assert diagnostic.last_result == "check_failed"
    assert diagnostic.active_sequence is None
    assert diagnostic.highest_seen_sequence == 0
    assert client.download_urls == ["memory://manifest/3"]
    assert client.closed


@pytest.mark.parametrize("status", [404, 410])
def test_missing_newest_manifest_can_fall_back_to_older_release(
    tmp_path: Path, status: int
) -> None:
    releases, assets = _release_batch(tmp_path, (1, 3, 2))
    client = _FakeReleaseClient(
        ReleasePage(False, '"missing"', releases),
        assets,
        asset_errors={"memory://manifest/3": _http_status_error(status)},
    )
    store, catalog = _runtime(tmp_path)
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    diagnostic = updater.check_once()

    assert diagnostic.last_result == "updated"
    assert diagnostic.active_sequence == diagnostic.highest_seen_sequence == 2
    assert client.download_urls == [
        "memory://manifest/3",
        "memory://manifest/2",
        "memory://package/2",
    ]


@pytest.mark.parametrize(
    ("outcome", "expected_result"),
    [("success", "no_releases"), ("failure", "check_failed"), ("interrupted", "checking")],
)
def test_background_cooldown_survives_restart_after_each_check_outcome(
    tmp_path: Path, outcome: str, expected_result: str
) -> None:
    started = datetime(2026, 9, 25, 12, tzinfo=UTC)
    error: BaseException | None = None
    if outcome == "failure":
        error = httpx.ConnectError("connection closed")
    elif outcome == "interrupted":
        error = SystemExit("simulated process interruption")
    client = _FakeReleaseClient(ReleasePage(False, '"empty"'), {}, list_error=error)
    store, catalog = _runtime(tmp_path)
    store.save_state(
        store.load_state().with_result(
            checked_at=(started - timedelta(days=1)).isoformat(),
            result="no_releases",
            etag='"previous"',
            product_version="0.2.1",
        )
    )
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
        clock=lambda: started,
    )

    if outcome == "interrupted":
        with pytest.raises(SystemExit, match="simulated process interruption"):
            updater.check_once()
    else:
        updater.check_once()

    state = store.load_state()
    assert state.last_checked_at == started.isoformat()
    assert state.last_checked_product_version == "0.2.1"
    assert state.last_result == expected_result
    assert client.etags == ['"previous"']
    assert state.etag == ('"empty"' if outcome == "success" else None)
    restarted_store = FirstPartySkillStore(store.personal_root)
    restarted = FirstPartySkillUpdateService(
        store=restarted_store,
        catalog=catalog,
        product_version="0.2.1",
        clock=lambda: started + timedelta(hours=6),
    )
    assert restarted._background_delay() == pytest.approx(18 * 60 * 60)


@pytest.mark.parametrize("elapsed_hours", [6, 24])
def test_background_start_waits_only_until_persisted_check_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, elapsed_hours: int
) -> None:
    started = datetime(2026, 9, 25, 12, tzinfo=UTC)
    store, catalog = _runtime(tmp_path)
    store.save_state(
        store.load_state().with_result(
            checked_at=started.isoformat(), result="check_failed", product_version="0.2.1"
        )
    )
    client = _FakeReleaseClient(ReleasePage(False, '"empty"'), {})
    updater = FirstPartySkillUpdateService(
        store=FirstPartySkillStore(store.personal_root),
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
        clock=lambda: started + timedelta(hours=elapsed_hours),
    )
    waits: list[float] = []

    def stop_at_next_wait(timeout: float) -> bool:
        if timeout <= 0:
            return False
        waits.append(timeout)
        updater._stop_event.set()
        return True

    monkeypatch.setattr(updater._stop_event, "wait", stop_at_next_wait)

    updater._run()

    if elapsed_hours == 6:
        assert not client.etags
        assert store.load_state().last_result == "check_failed"
        assert waits == [18 * 60 * 60]
    else:
        assert client.etags == [None]
        assert store.load_state().last_result == "no_releases"
        assert waits == [24 * 60 * 60]


@pytest.mark.parametrize(
    ("checked_at", "previous_product", "expected_delay"),
    [
        ("2026-09-25T12:00:00+00:00", "0.2.0", 0),
        ("invalid timestamp", "0.2.1", 0),
        ("2026-09-25T12:00:00", "0.2.1", 0),
        ("2026-10-25T12:00:00+00:00", "0.2.1", 24 * 60 * 60),
    ],
)
def test_background_cooldown_handles_upgrade_invalid_time_and_clock_rollback(
    tmp_path: Path, checked_at: str, previous_product: str, expected_delay: float
) -> None:
    store, catalog = _runtime(tmp_path)
    store.save_state(
        store.load_state().with_result(
            checked_at=checked_at, result="no_update", product_version=previous_product
        )
    )
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        clock=lambda: datetime(2026, 9, 25, 18, tzinfo=UTC),
    )

    assert updater._background_delay() == pytest.approx(expected_delay)


@pytest.mark.parametrize("previous_product", ["0.2.0", "0.2.1"])
def test_failed_package_download_clears_etag_before_explicit_retry(
    tmp_path: Path, previous_product: str
) -> None:
    releases, assets = _release_batch(tmp_path, (1,))
    page = ReleasePage(False, '"new"', releases)
    failing = _FakeReleaseClient(
        page, assets, asset_errors={"memory://package/1": _http_status_error(500)}
    )
    store, catalog = _runtime(tmp_path)
    store.save_state(
        store.load_state().with_result(
            checked_at="2026-09-24T12:00:00+00:00",
            result="no_compatible_release",
            etag='"old"',
            product_version=previous_product,
        )
    )
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: failing,
    )

    assert updater.check_once().last_result == "check_failed"
    assert failing.etags == ['"old"' if previous_product == "0.2.1" else None]
    assert store.load_state().etag is None
    retry_client = _FakeReleaseClient(page, assets)
    retry = FirstPartySkillUpdateService(
        store=FirstPartySkillStore(store.personal_root),
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: retry_client,
    )

    diagnostic = retry.check_once()

    assert diagnostic.last_result == "updated"
    assert retry_client.etags == [None]
    assert retry_client.download_urls == ["memory://manifest/1", "memory://package/1"]
    assert store.load_state().etag == '"new"'


def test_successful_not_modified_response_keeps_etag_for_next_check(tmp_path: Path) -> None:
    store, catalog = _runtime(tmp_path)
    store.save_state(
        store.load_state().with_result(
            checked_at="2026-09-24T12:00:00+00:00",
            result="no_releases",
            etag='"unchanged"',
            product_version="0.2.1",
        )
    )
    client = _FakeReleaseClient(ReleasePage(True, '"unchanged"'), {})
    updater = FirstPartySkillUpdateService(
        store=store,
        catalog=catalog,
        product_version="0.2.1",
        client_factory=lambda: client,
    )

    assert updater.check_once().last_result == "not_modified"
    assert store.load_state().etag == '"unchanged"'
    assert updater.check_once().last_result == "not_modified"
    assert client.etags == ['"unchanged"', '"unchanged"']
    assert not client.download_urls

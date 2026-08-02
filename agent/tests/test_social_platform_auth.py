from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from agent.tools.builtin.social_platforms import (
    SocialAuthChallenge,
    SocialAuthInteraction,
    SocialAuthManager,
    SocialAuthMode,
    SocialAuthStatus,
    SocialPlatform,
    SocialProviderError,
)
from agent.tools.builtin.social_platforms.connectors import (
    BilibiliAuthHandler,
    BilibiliCredentialStore,
    BilibiliQrLoginClient,
    XiaohongshuAuthHandler,
)
from agent.tools.builtin.social_platforms.connectors.bilibili_auth import (
    LocalQrPresenter,
    _IsolatedSdkQrLoginSession,
    _new_qr_login_session,
)
from bilibili_api import Credential, login_v2


class FakeAuthHandler:
    platform: ClassVar[SocialPlatform] = "bilibili"
    mode: ClassVar[SocialAuthMode] = "optional"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.closed = False
        self.cancel_calls = 0

    def status(self) -> SocialAuthStatus:
        return SocialAuthStatus(
            platform="bilibili",
            mode="optional",
            state="anonymous",
            material_present=False,
        )

    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> SocialAuthStatus:
        del interaction
        self.started.set()
        self.cancelled.wait(timeout=2)
        if cancelled():
            raise SocialProviderError("cancelled")
        return self.status()

    def cancel_login(self) -> None:
        self.cancel_calls += 1
        self.cancelled.set()

    def close(self) -> None:
        self.closed = True
        self.cancelled.set()


def test_auth_manager_routes_status_and_rejects_duplicate_login() -> None:
    handler = FakeAuthHandler()
    manager = SocialAuthManager((handler,))
    errors: list[BaseException] = []

    def login() -> None:
        try:
            manager.login("bilibili")
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=login)
    thread.start()
    assert handler.started.wait(timeout=1)
    assert manager.status("bilibili").state == "login_in_progress"

    with pytest.raises(SocialProviderError, match="already running"):
        manager.login("bilibili")

    manager.cancel_login("bilibili")
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], SocialProviderError)
    assert handler.cancel_calls == 1


def test_auth_manager_close_cancels_and_closes_handlers() -> None:
    handler = FakeAuthHandler()
    manager = SocialAuthManager((handler,))

    manager.close()

    assert handler.closed is True
    assert handler.cancelled.is_set()


class FakeXiaohongshuProcess:
    def __init__(self, *, return_code: int = 0, material_after_login: bool = True) -> None:
        self.return_code = return_code
        self.material_after_login = material_after_login
        self.has_login_material = False
        self.closed = False

    def run_login(self) -> int:
        self.has_login_material = self.material_after_login
        return self.return_code

    def close(self) -> None:
        self.closed = True


def test_xiaohongshu_auth_handler_uses_platform_login_and_verifier() -> None:
    process = FakeXiaohongshuProcess()
    handler = XiaohongshuAuthHandler(process, verify_login=lambda: True)

    assert handler.status().state == "anonymous"
    result = handler.login(cancelled=lambda: False)

    assert result.state == "credential_present"
    assert result.material_present is True


def test_xiaohongshu_auth_handler_does_not_accept_unverified_material() -> None:
    process = FakeXiaohongshuProcess()
    handler = XiaohongshuAuthHandler(process, verify_login=lambda: False)

    with pytest.raises(Exception, match="was not completed"):
        handler.login(cancelled=lambda: False)


class FakeCredentialBackend:
    def __init__(self, *, error: str | None = None) -> None:
        self.value: str | None = None
        self.error = error

    def get(self, name: str) -> object | None:
        del name
        return SimpleNamespace(value=self.value) if self.value is not None else None

    def status(self, name: str) -> object:
        del name
        return SimpleNamespace(configured=self.value is not None, error=self.error)

    def set(self, name: str, value: str) -> None:
        del name
        self.value = value


def _credential() -> Credential:
    return Credential(
        sessdata="sessdata-secret",
        bili_jct="csrf-secret",
        buvid3="device-secret",
        dedeuserid="42",
        ac_time_value="refresh-secret",
    )


def test_bilibili_credential_store_round_trips_one_versioned_keyring_blob() -> None:
    backend = FakeCredentialBackend()
    store = BilibiliCredentialStore(backend)

    store.save(_credential())
    restored = store.load()

    assert restored is not None
    assert restored.sessdata == "sessdata-secret"
    assert restored.bili_jct == "csrf-secret"
    assert backend.value is not None
    assert '"schema_version":1' in backend.value


def test_bilibili_credential_store_rejects_unknown_or_incomplete_schema() -> None:
    backend = FakeCredentialBackend()
    store = BilibiliCredentialStore(backend)
    backend.value = '{"schema_version":1,"credential":{"unexpected":"secret"}}'

    with pytest.raises(ValueError, match="unexpected fields"):
        store.load()

    backend.value = '{"schema_version":1,"credential":{"bili_jct":"secret"}}'
    with pytest.raises(ValueError, match="no SESSDATA"):
        store.load()


class FakeCredentialTarget:
    def __init__(self) -> None:
        self.credential = Credential()

    @property
    def authenticated(self) -> bool:
        return self.credential.has_sessdata()

    def set_credential(self, credential: Credential) -> None:
        self.credential = credential


class FakeLoginClient:
    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> Credential:
        del interaction
        assert cancelled() is False
        return _credential()


def test_bilibili_auth_handler_persists_and_restores_without_exposing_values() -> None:
    backend = FakeCredentialBackend()
    first_target = FakeCredentialTarget()
    first = BilibiliAuthHandler(
        first_target,
        store=BilibiliCredentialStore(backend),
        login_client=FakeLoginClient(),
    )

    result = first.login(cancelled=lambda: False)

    assert result == SocialAuthStatus(
        platform="bilibili",
        mode="optional",
        state="credential_present",
        material_present=True,
    )
    assert "sessdata-secret" not in repr(result)

    restored_target = FakeCredentialTarget()
    restored = BilibiliAuthHandler(
        restored_target,
        store=BilibiliCredentialStore(backend),
        login_client=FakeLoginClient(),
    )
    assert restored.status().state == "credential_present"
    assert restored_target.credential.sessdata == "sessdata-secret"


def test_bilibili_auth_handler_reports_keyring_unavailable_without_starting_login() -> None:
    backend = FakeCredentialBackend(error="backend unavailable")
    handler = BilibiliAuthHandler(
        FakeCredentialTarget(),
        store=BilibiliCredentialStore(backend),
        login_client=FakeLoginClient(),
    )

    status = handler.status()

    assert status.state == "unavailable"
    assert status.error == "credential_store_unavailable"
    with pytest.raises(Exception, match="credential store is unavailable"):
        handler.login(cancelled=lambda: False)


class FakeQrPicture:
    def to_file(self, path: str) -> object:
        Path(path).write_bytes(b"qr-image")
        return self


class FakeQrSession:
    def __init__(self) -> None:
        self._events = iter(("scan", "done"))
        self._last = ""

    async def generate_qrcode(self) -> None:
        return None

    def get_qrcode_picture(self) -> FakeQrPicture:
        return FakeQrPicture()

    async def check_state(self) -> str:
        self._last = next(self._events)
        return self._last

    def has_done(self) -> bool:
        return self._last == "done"

    def get_credential(self) -> Credential:
        return _credential()


class FakeQrPresenter:
    def __init__(self) -> None:
        self.shown = False

    @contextmanager
    def show(
        self,
        picture: object,
        *,
        interaction: SocialAuthInteraction | None = None,
    ) -> Iterator[None]:
        del interaction
        assert isinstance(picture, FakeQrPicture)
        self.shown = True
        yield


async def _no_sleep(_: float) -> None:
    return None


def test_bilibili_qr_client_uses_local_presenter_and_never_prints_qr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = FakeQrSession()
    presenter = FakeQrPresenter()
    client = BilibiliQrLoginClient(
        presenter=presenter,
        session_factory=lambda: session,
        async_sleep=_no_sleep,
        monotonic=lambda: 0.0,
    )

    credential = client.login(cancelled=lambda: False)

    assert credential.sessdata == "sessdata-secret"
    assert presenter.shown is True
    assert capsys.readouterr() == ("", "")


def test_bilibili_sdk_fixed_qr_file_is_isolated_and_removed() -> None:
    original_tempfile = SimpleNamespace(name="original")
    module = SimpleNamespace(tempfile=original_tempfile)

    class SdkSession(FakeQrSession):
        generated_path: Path | None = None

        async def generate_qrcode(self) -> None:
            self.generated_path = Path(module.tempfile.gettempdir()) / "qrcode.png"
            self.generated_path.write_bytes(b"sdk-temporary-qr")

    delegate = SdkSession()
    session = _IsolatedSdkQrLoginSession(module, delegate)

    asyncio.run(session.generate_qrcode())

    assert module.tempfile is original_tempfile
    assert delegate.generated_path is not None
    assert not delegate.generated_path.exists()


def test_bilibili_sdk_qr_login_uses_tv_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[login_v2.QrCodeLoginChannel] = []
    delegate = FakeQrSession()

    def create_session(*, platform: login_v2.QrCodeLoginChannel) -> FakeQrSession:
        captured.append(platform)
        return delegate

    monkeypatch.setattr(login_v2, "QrCodeLogin", create_session)

    session = _new_qr_login_session()

    assert captured == [login_v2.QrCodeLoginChannel.TV]
    assert session._delegate is delegate


def test_local_qr_presenter_uses_unique_temporary_file_and_removes_it() -> None:
    opened: list[Path] = []
    presenter = LocalQrPresenter(opener=opened.append)

    with presenter.show(FakeQrPicture()):
        assert len(opened) == 1
        assert opened[0].is_file()
        assert opened[0].read_bytes() == b"qr-image"

    assert not opened[0].exists()


def test_local_qr_presenter_publishes_and_clears_desktop_challenge_without_opener() -> None:
    opened: list[Path] = []
    published: list[SocialAuthChallenge] = []
    clear_calls = 0

    def clear() -> None:
        nonlocal clear_calls
        clear_calls += 1

    presenter = LocalQrPresenter(opener=opened.append)
    interaction = SocialAuthInteraction(
        publish_challenge=published.append,
        clear_challenge=clear,
    )

    with presenter.show(FakeQrPicture(), interaction=interaction):
        assert opened == []
        assert published == [
            SocialAuthChallenge(
                kind="qr_image",
                media_type="image/png",
                content=b"qr-image",
            )
        ]
        assert clear_calls == 0

    assert clear_calls == 1

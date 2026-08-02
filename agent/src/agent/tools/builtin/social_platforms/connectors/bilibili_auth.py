"""Optional Bilibili QR login with local display and keyring persistence."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, ClassVar, Protocol

from bilibili_api import Credential

from runtime_config import KeyringSource

from ..auth import (
    SocialAuthChallenge,
    SocialAuthInteraction,
    SocialAuthMode,
    SocialAuthStatus,
)
from ..models import (
    SocialAuthRequiredError,
    SocialConnectorUnavailableError,
    SocialPlatform,
    SocialProviderError,
)

BILIBILI_CREDENTIAL_KEY = "social.bilibili.credential.v1"
_CREDENTIAL_FIELDS = (
    "sessdata",
    "bili_jct",
    "buvid3",
    "buvid4",
    "dedeuserid",
    "ac_time_value",
)
_QR_GENERATION_LOCK = threading.Lock()


class BilibiliCredentialTarget(Protocol):
    @property
    def authenticated(self) -> bool: ...

    def set_credential(self, credential: Credential) -> None: ...


class CredentialBackend(Protocol):
    def get(self, name: str) -> Any | None: ...

    def status(self, name: str) -> Any: ...

    def set(self, name: str, value: str) -> None: ...


class BilibiliCredentialStore:
    """Store one versioned credential blob without registering it as an API key."""

    def __init__(self, backend: CredentialBackend | None = None) -> None:
        self._backend = backend or KeyringSource()

    def status(self) -> tuple[bool, bool]:
        status = self._backend.status(BILIBILI_CREDENTIAL_KEY)
        return bool(status.configured), status.error is None

    def load(self) -> Credential | None:
        value = self._backend.get(BILIBILI_CREDENTIAL_KEY)
        if value is None:
            return None
        try:
            payload = json.loads(value.value)
        except (AttributeError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError("stored Bilibili credential is invalid") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("stored Bilibili credential has an unsupported schema")
        raw_credential = payload.get("credential")
        if not isinstance(raw_credential, dict) or not set(raw_credential).issubset(
            _CREDENTIAL_FIELDS
        ):
            raise ValueError("stored Bilibili credential has unexpected fields")
        fields: dict[str, str] = {}
        for name, raw in raw_credential.items():
            if not isinstance(raw, str) or not raw:
                raise ValueError("stored Bilibili credential has an invalid field")
            fields[name] = raw
        if not fields.get("sessdata"):
            raise ValueError("stored Bilibili credential has no SESSDATA")
        return Credential(**fields)

    def save(self, credential: Credential) -> None:
        if not _has_sessdata(credential):
            raise ValueError("Bilibili login returned no SESSDATA")
        fields = {
            name: value
            for name in _CREDENTIAL_FIELDS
            if isinstance((value := getattr(credential, name, None)), str) and value
        }
        payload = json.dumps(
            {"schema_version": 1, "credential": fields},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._backend.set(BILIBILI_CREDENTIAL_KEY, payload)


class QrPicture(Protocol):
    def to_file(self, path: str) -> Any: ...


class QrPresenter(Protocol):
    def show(
        self,
        picture: QrPicture,
        *,
        interaction: SocialAuthInteraction | None = None,
    ) -> AbstractContextManager[None]: ...


class LocalQrPresenter:
    def __init__(self, *, opener: Callable[[Path], None] | None = None) -> None:
        self._opener = opener or _open_local_image

    @contextmanager
    def show(
        self,
        picture: QrPicture,
        *,
        interaction: SocialAuthInteraction | None = None,
    ) -> Iterator[None]:
        with tempfile.TemporaryDirectory(prefix="agent-friend-bilibili-login-") as temporary:
            path = Path(temporary) / "bilibili-login-qr.png"
            try:
                picture.to_file(str(path))
                if os.name != "nt":
                    path.chmod(0o600)
                if interaction is None:
                    self._opener(path)
                else:
                    interaction.publish_challenge(
                        SocialAuthChallenge(
                            kind="qr_image",
                            media_type="image/png",
                            content=path.read_bytes(),
                        )
                    )
            except Exception as exc:
                raise SocialConnectorUnavailableError(
                    "Bilibili QR code could not be prepared"
                ) from exc
            try:
                yield
            finally:
                if interaction is not None:
                    interaction.clear_challenge()


class QrLoginSession(Protocol):
    async def generate_qrcode(self) -> None: ...

    def get_qrcode_picture(self) -> QrPicture: ...

    async def check_state(self) -> Any: ...

    def has_done(self) -> bool: ...

    def get_credential(self) -> Credential: ...


class BilibiliQrLoginClient:
    def __init__(
        self,
        *,
        presenter: QrPresenter | None = None,
        session_factory: Callable[[], QrLoginSession] | None = None,
        poll_interval_seconds: float = 1.0,
        timeout_seconds: float = 180.0,
        monotonic: Callable[[], float] = time.monotonic,
        async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._presenter = presenter or LocalQrPresenter()
        self._session_factory = session_factory or _new_qr_login_session
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._monotonic = monotonic
        self._async_sleep = async_sleep

    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> Credential:
        return asyncio.run(self._login(cancelled=cancelled, interaction=interaction))

    async def _login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None,
    ) -> Credential:
        session = self._session_factory()
        with _QR_GENERATION_LOCK:
            await session.generate_qrcode()
            picture = session.get_qrcode_picture()
        deadline = self._monotonic() + self._timeout_seconds
        with self._presenter.show(picture, interaction=interaction):
            while self._monotonic() < deadline:
                if cancelled():
                    raise SocialProviderError("Bilibili login was cancelled")
                event = await session.check_state()
                value = str(getattr(event, "value", event)).lower()
                if value == "done" or session.has_done():
                    credential = session.get_credential()
                    if not _has_sessdata(credential):
                        raise SocialAuthRequiredError("Bilibili login returned no session")
                    return credential
                if value == "timeout":
                    raise SocialAuthRequiredError("Bilibili login QR code expired")
                await self._async_sleep(self._poll_interval_seconds)
        raise SocialAuthRequiredError("Bilibili login QR code expired")


class BilibiliLoginClient(Protocol):
    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> Credential: ...


class BilibiliAuthHandler:
    platform: ClassVar[SocialPlatform] = "bilibili"
    mode: ClassVar[SocialAuthMode] = "optional"

    def __init__(
        self,
        target: BilibiliCredentialTarget,
        *,
        store: BilibiliCredentialStore | None = None,
        login_client: BilibiliLoginClient | None = None,
    ) -> None:
        self._target = target
        self._store = store or BilibiliCredentialStore()
        self._login_client = login_client or BilibiliQrLoginClient()
        self._restore_error: str | None = None
        self._restore()

    def status(self) -> SocialAuthStatus:
        present, available = self._store.status()
        if not available:
            return SocialAuthStatus(
                platform=self.platform,
                mode=self.mode,
                state="unavailable",
                material_present=present,
                error="credential_store_unavailable",
            )
        if self._restore_error is not None:
            return SocialAuthStatus(
                platform=self.platform,
                mode=self.mode,
                state="unavailable",
                material_present=present,
                error=self._restore_error,
            )
        material_present = present or self._target.authenticated
        return SocialAuthStatus(
            platform=self.platform,
            mode=self.mode,
            state="credential_present" if material_present else "anonymous",
            material_present=material_present,
        )

    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> SocialAuthStatus:
        _, available = self._store.status()
        if not available:
            raise SocialConnectorUnavailableError("Bilibili credential store is unavailable")
        credential = self._login_client.login(
            cancelled=cancelled,
            interaction=interaction,
        )
        if cancelled():
            raise SocialProviderError("Bilibili login was cancelled")
        try:
            self._store.save(credential)
        except Exception as exc:
            raise SocialConnectorUnavailableError("Bilibili credential could not be saved") from exc
        self._target.set_credential(credential)
        self._restore_error = None
        return self.status()

    def cancel_login(self) -> None:
        return None

    def close(self) -> None:
        return None

    def _restore(self) -> None:
        try:
            credential = self._store.load()
        except RuntimeError:
            self._restore_error = "credential_store_unavailable"
            return
        except ValueError:
            self._restore_error = "stored_credential_invalid"
            return
        if credential is not None:
            self._target.set_credential(credential)


class _IsolatedSdkQrLoginSession:
    """Contain the pinned SDK's fixed ``qrcode.png`` inside a unique temp dir."""

    def __init__(self, module: Any, delegate: QrLoginSession) -> None:
        self._module = module
        self._delegate = delegate

    async def generate_qrcode(self) -> None:
        original_tempfile = self._module.tempfile
        with tempfile.TemporaryDirectory(prefix="agent-friend-bilibili-sdk-qr-") as temporary:
            self._module.tempfile = _TempfileProxy(temporary)
            try:
                await self._delegate.generate_qrcode()
            finally:
                self._module.tempfile = original_tempfile

    def get_qrcode_picture(self) -> QrPicture:
        return self._delegate.get_qrcode_picture()

    async def check_state(self) -> Any:
        return await self._delegate.check_state()

    def has_done(self) -> bool:
        return self._delegate.has_done()

    def get_credential(self) -> Credential:
        return self._delegate.get_credential()


class _TempfileProxy:
    def __init__(self, directory: str) -> None:
        self._directory = directory

    def gettempdir(self) -> str:
        return self._directory


def _new_qr_login_session() -> _IsolatedSdkQrLoginSession:
    from bilibili_api import login_v2

    delegate = login_v2.QrCodeLogin(platform=login_v2.QrCodeLoginChannel.TV)
    return _IsolatedSdkQrLoginSession(login_v2, delegate)


def _has_sessdata(credential: Credential) -> bool:
    return isinstance(credential.sessdata, str) and bool(credential.sessdata)


def _open_local_image(path: Path) -> None:
    if sys.platform == "win32":
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("Windows local file opener is unavailable")
        startfile(str(path))
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    if shutil.which(command[0]) is None:
        raise OSError("local file opener is unavailable")
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


__all__ = [
    "BILIBILI_CREDENTIAL_KEY",
    "BilibiliAuthHandler",
    "BilibiliCredentialStore",
    "BilibiliQrLoginClient",
    "LocalQrPresenter",
]

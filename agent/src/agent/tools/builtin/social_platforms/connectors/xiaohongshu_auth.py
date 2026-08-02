"""Local login handler for the locked Xiaohongshu connector."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Protocol

from ..auth import SocialAuthInteraction, SocialAuthMode, SocialAuthStatus
from ..models import SocialAuthRequiredError, SocialPlatform, SocialProviderError


class XiaohongshuLoginProcess(Protocol):
    @property
    def has_login_material(self) -> bool: ...

    def run_login(self) -> int: ...

    def close(self) -> None: ...


class XiaohongshuAuthHandler:
    platform: ClassVar[SocialPlatform] = "xiaohongshu"
    mode: ClassVar[SocialAuthMode] = "required"

    def __init__(
        self,
        process: XiaohongshuLoginProcess,
        *,
        verify_login: Callable[[], bool],
    ) -> None:
        self._process = process
        self._verify_login = verify_login

    def status(self) -> SocialAuthStatus:
        present = self._process.has_login_material
        return SocialAuthStatus(
            platform=self.platform,
            mode=self.mode,
            state="credential_present" if present else "anonymous",
            material_present=present,
        )

    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> SocialAuthStatus:
        del interaction
        if cancelled():
            raise SocialProviderError("Xiaohongshu login was cancelled")
        return_code = self._process.run_login()
        if cancelled():
            raise SocialProviderError("Xiaohongshu login was cancelled")
        if return_code != 0:
            raise SocialProviderError("Xiaohongshu login did not complete")
        if not self._process.has_login_material or not self._verify_login():
            raise SocialAuthRequiredError("Xiaohongshu login was not completed")
        return self.status()

    def cancel_login(self) -> None:
        self._process.close()

    def close(self) -> None:
        self._process.close()


__all__ = ["XiaohongshuAuthHandler", "XiaohongshuLoginProcess"]

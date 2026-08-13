"""Desktop setting adapter for the Agent self-presentation prompt section."""

from __future__ import annotations

import logging
from threading import Lock

from agent import SelfPresentationMode
from runtime_config import DesktopSettingsReader

logger = logging.getLogger(__name__)


class DesktopSelfPresentationModeProvider:
    """Resolve ``immersiveMode`` once per prompt snapshot with last-valid fallback."""

    def __init__(self, reader: DesktopSettingsReader) -> None:
        self._reader = reader
        self._lock = Lock()
        self._last_valid: SelfPresentationMode = "immersive"

    def __call__(self) -> SelfPresentationMode:
        with self._lock:
            settings = self._reader.read()
            if settings is None:
                logger.warning(
                    "self presentation: settings unreadable; using last valid mode=%s",
                    self._last_valid,
                )
                return self._last_valid

            value = settings.get("immersiveMode", True)
            if not isinstance(value, bool):
                logger.warning(
                    "self presentation: immersiveMode is not boolean; using last valid mode=%s",
                    self._last_valid,
                )
                return self._last_valid

            self._last_valid = "immersive" if value else "transparent"
            return self._last_valid

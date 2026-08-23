"""Desktop setting adapter for Reminder notification fan-out."""

from __future__ import annotations

import logging
from threading import Lock

from agent.todos import ReminderNotificationMode

from runtime_config import DesktopSettingsReader

logger = logging.getLogger(__name__)


class DesktopReminderNotificationModeProvider:
    """Read the persisted mode with a conservative last-valid fallback."""

    def __init__(self, reader: DesktopSettingsReader) -> None:
        self._reader = reader
        self._lock = Lock()
        self._last_valid = ReminderNotificationMode.SMART

    def __call__(self) -> ReminderNotificationMode:
        with self._lock:
            settings = self._reader.read()
            if settings is None:
                logger.warning(
                    "reminder notification mode: settings unreadable; using last valid mode=%s",
                    self._last_valid.value,
                )
                return self._last_valid
            raw = settings.get("reminderNotificationMode", ReminderNotificationMode.SMART.value)
            try:
                mode = ReminderNotificationMode(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "reminder notification mode: invalid value; using last valid mode=%s",
                    self._last_valid.value,
                )
                return self._last_valid
            self._last_valid = mode
            return mode


__all__ = ["DesktopReminderNotificationModeProvider"]

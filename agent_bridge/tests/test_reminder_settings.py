from __future__ import annotations

import json
from pathlib import Path

from agent.todos import ReminderNotificationMode
from agent_bridge.reminder_settings import DesktopReminderNotificationModeProvider

from runtime_config import DesktopSettingsReader


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_reminder_notification_mode_defaults_to_smart(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, {"settings": {}})

    provider = DesktopReminderNotificationModeProvider(DesktopSettingsReader(path))

    assert provider() is ReminderNotificationMode.SMART


def test_reminder_notification_mode_reads_all_and_keeps_last_valid(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, {"settings": {"reminderNotificationMode": "all"}})
    provider = DesktopReminderNotificationModeProvider(DesktopSettingsReader(path))

    assert provider() is ReminderNotificationMode.ALL
    _write(path, {"settings": {"reminderNotificationMode": "invalid"}})
    assert provider() is ReminderNotificationMode.ALL
    path.write_text("not-json", encoding="utf-8")
    assert provider() is ReminderNotificationMode.ALL

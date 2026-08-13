from __future__ import annotations

from pathlib import Path

from agent_bridge.self_presentation import DesktopSelfPresentationModeProvider

from runtime_config import DesktopSettingsReader


def _write(path: Path, raw: str) -> None:
    path.write_text(raw, encoding="utf-8")


def test_provider_defaults_missing_setting_to_immersive(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, '{"settings": {}}')

    provider = DesktopSelfPresentationModeProvider(DesktopSettingsReader(path))

    assert provider() == "immersive"


def test_provider_reads_each_turn_and_maps_false_to_transparent(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, '{"settings": {"immersiveMode": true}}')
    provider = DesktopSelfPresentationModeProvider(DesktopSettingsReader(path))

    assert provider() == "immersive"
    _write(path, '{"settings": {"immersiveMode": false}}')
    assert provider() == "transparent"


def test_provider_keeps_last_valid_value_on_transient_failure(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _write(path, '{"settings": {"immersiveMode": false}}')
    provider = DesktopSelfPresentationModeProvider(DesktopSettingsReader(path))

    assert provider() == "transparent"
    _write(path, "not-json")
    assert provider() == "transparent"
    _write(path, '{"settings": {"immersiveMode": "false"}}')
    assert provider() == "transparent"


def test_provider_cold_read_failure_fails_closed_to_immersive(tmp_path: Path) -> None:
    provider = DesktopSelfPresentationModeProvider(DesktopSettingsReader(tmp_path / "missing.json"))

    assert provider() == "immersive"

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

ASSET = Path(__file__).parents[1] / "src/agent/skills/builtin_assets/screenshot/scripts"


def test_python_default_uses_host_directory_without_overriding_explicit_or_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = runpy.run_path(str(ASSET / "take_screenshot.py"))
    output = tmp_path / "managed"
    monkeypatch.setenv("AGENT_FRIEND_SCREENSHOT_OUTPUT_DIR", str(output))
    for platform in ("Windows", "Darwin", "Linux"):
        assert module["default_dir"](platform) == output


@pytest.mark.skipif(os.name != "nt", reason="PowerShell helper is Windows-only")
def test_powershell_default_explicit_and_temp_paths(tmp_path: Path) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell is not None
    script = tmp_path / "check.ps1"
    script.write_text(
        """param([string]$Source, [string]$Output)
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$null, [ref]$null)
$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $false) | ForEach-Object { Invoke-Expression $_.Extent.Text }
$Format = 'png'
$Mode = 'default'
$Path = ''
$env:AGENT_FRIEND_SCREENSHOT_OUTPUT_DIR = $Output
$managed = Resolve-OutputPath
$Path = Join-Path $Output 'explicit.png'
$explicit = Resolve-OutputPath
$Path = ''
$Mode = 'temp'
$temporary = Resolve-OutputPath
Remove-Item Env:AGENT_FRIEND_SCREENSHOT_OUTPUT_DIR
$fallback = Get-DefaultDirectory
@{managed=$managed; explicit=$explicit; temporary=$temporary; fallback=$fallback} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    output = tmp_path / "managed"
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Source",
            str(ASSET / "take_screenshot.ps1"),
            "-Output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    data = json.loads(result.stdout)
    assert Path(data["managed"]).parent == output
    assert Path(data["explicit"]) == output / "explicit.png"
    assert Path(data["temporary"]).parent != output
    assert Path(data["fallback"]).is_absolute()

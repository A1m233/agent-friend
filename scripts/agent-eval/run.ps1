$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m agent_eval @args

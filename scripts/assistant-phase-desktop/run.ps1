$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# Synthetic history, queued-to-running header, geometry/hover and activity tail; no LLM calls.
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/assistant-phase-desktop/run.py @args
exit $LASTEXITCODE

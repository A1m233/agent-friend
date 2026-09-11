$ErrorActionPreference = "Stop"
# Uses the current deepseek-flash model; requires explicit --allow-llm.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/assistant-phase-smoke/run.py @args
exit $LASTEXITCODE

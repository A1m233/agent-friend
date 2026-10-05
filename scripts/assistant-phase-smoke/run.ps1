$ErrorActionPreference = "Stop"
# 151: lookup_fixture returns the synthetic image; no native attachment upload.
# Uses the current deepseek-flash model; requires explicit --allow-llm.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/assistant-phase-smoke/run.py @args
exit $LASTEXITCODE

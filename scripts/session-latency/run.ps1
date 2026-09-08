$ErrorActionPreference = "Stop"
# prepare/scroll/lock-compare observe; real/isolated serve/long-runtime require separate LLM authorization.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/session-latency/run.py @args
exit $LASTEXITCODE

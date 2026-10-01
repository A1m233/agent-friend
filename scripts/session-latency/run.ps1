$ErrorActionPreference = "Stop"
# prepare/scroll/lock-compare observe; real/isolated serve/long-runtime require separate LLM authorization.
# offline sends on disposable copies, blocks network, and reports derived storage costs.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/session-latency/run.py @args
exit $LASTEXITCODE

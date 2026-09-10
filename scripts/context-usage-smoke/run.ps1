$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
# Four real requests, at most five, through isolated usage/rollout smoke.
& uv run python scripts/context-usage-smoke/run.py @args
exit $LASTEXITCODE

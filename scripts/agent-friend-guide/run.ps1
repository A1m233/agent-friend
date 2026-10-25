$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# verify is read-only; non-default --guide-repo requires --guide-commit.
# Success and failure include the checkout identity; no fetch or update is performed.

Set-Location (Join-Path $PSScriptRoot "..\..")

& uv run python scripts/agent-friend-guide/run.py @args
exit $LASTEXITCODE

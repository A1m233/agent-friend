$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Set-Location (Join-Path $PSScriptRoot "..\..")

& uv run python scripts/agent-friend-guide/run.py @args
exit $LASTEXITCODE

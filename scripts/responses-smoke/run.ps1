$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/responses-smoke/run.py @args
exit $LASTEXITCODE

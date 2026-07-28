$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python scripts/prompt-cache-smoke/run.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

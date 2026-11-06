$ErrorActionPreference = "Stop"
# --section exposure observes four contact decisions without external messaging.
# --section social-identity uses the configured memory model and an isolated database.
# --section memory-commit checks identity synchronization and receipts; max 10 model calls.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python scripts/companionship-smoke/run.py @args
exit $LASTEXITCODE

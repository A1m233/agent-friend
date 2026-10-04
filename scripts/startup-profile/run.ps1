$ErrorActionPreference = "Stop"
# installed-evidence checks isolated profile routing on Windows; read-only commands are cross-platform.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/startup-profile/run.py @args
exit $LASTEXITCODE

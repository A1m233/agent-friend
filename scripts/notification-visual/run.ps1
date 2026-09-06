$ErrorActionPreference = "Stop"
# Settings target Notifications; explicit restart-bridge uses the existing supervisor guard.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
. (Join-Path $PSScriptRoot "..\frontend\_ensure-node.ps1")
Ensure-NodePnpm
node scripts/notification-visual/run.mjs @args
exit $LASTEXITCODE

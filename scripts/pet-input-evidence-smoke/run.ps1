$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# inspect-animation reads two existing ticker/canvas samples without changing the app or saving images.
# Script-execution overrides can lose pending rAF callbacks and are intentionally unsupported.
Set-Location (Join-Path $PSScriptRoot "..\..")
. (Join-Path $PSScriptRoot "..\frontend\_ensure-node.ps1")
Ensure-NodePnpm
node (Join-Path $PSScriptRoot "run.mjs") @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

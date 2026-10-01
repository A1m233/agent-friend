$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& node (Join-Path $PSScriptRoot "probe.mjs") @args
exit $LASTEXITCODE

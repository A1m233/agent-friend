$ErrorActionPreference = "Stop"
# Build admission reclaims only necessary safe caches; --automatic controls post-build maintenance.
# inventory --root discovers history without adoption; reconcile --outcome records confirmed exit.
# adopt --cargo-evidence verifies receipt-less Cargo caches; gc --only-object-id narrows policy targets.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
. (Join-Path $PSScriptRoot "..\_lib\python312.ps1")
$python = Get-BuildPython
& $python -B (Join-Path $PSScriptRoot "run.py") @args
exit $LASTEXITCODE

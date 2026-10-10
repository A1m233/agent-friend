$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
. (Join-Path $PSScriptRoot "..\_lib\python312.ps1")
$python = Get-BuildPython
& $python -B (Join-Path $PSScriptRoot "run.py") @args
exit $LASTEXITCODE

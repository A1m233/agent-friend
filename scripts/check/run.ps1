$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

# Planning/frontend-only checks must not sync the Python workspace.
. (Join-Path $PSScriptRoot "..\_lib\python312.ps1")
$checkPython = Get-BuildPython
& $checkPython -B scripts/check/run.py @args
exit $LASTEXITCODE

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "Node 22+ is required. Run .\scripts\setup\run.ps1 first."
}

node (Join-Path $PSScriptRoot "run.mjs") @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

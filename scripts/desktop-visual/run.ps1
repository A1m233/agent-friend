$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Error "uv is required. Run .\scripts\setup\run.ps1 first."
}

uv run python scripts/desktop-visual/run.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

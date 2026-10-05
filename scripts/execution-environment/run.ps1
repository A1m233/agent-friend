$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# Smoke exercises PDF/native tools and basic spreadsheet/DOCX file workflows offline.
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/execution-environment/run.py @args
exit $LASTEXITCODE

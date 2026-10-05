$ErrorActionPreference = "Stop"
# 151: three synthetic tool-image requests; no Files API uploads.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

& uv run python scripts/deepseek-vision-smoke/run.py @args
exit $LASTEXITCODE

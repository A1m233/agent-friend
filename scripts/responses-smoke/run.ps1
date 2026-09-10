$ErrorActionPreference = "Stop"
# --scenario selects tool-loop (default) or nonstream-json; arguments pass through.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/responses-smoke/run.py @args
exit $LASTEXITCODE

# --custom: generic Responses; --custom-chat: generic Chat tool loop (Kimi K2.6).

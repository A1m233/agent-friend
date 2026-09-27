$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# Pass through --scenario long-session for the bounded 144 observation.
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python scripts/context-recovery-smoke/run.py @args
exit $LASTEXITCODE

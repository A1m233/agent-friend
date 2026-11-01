$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# Pass through --scenario long-session for the bounded 144 observation.
# Synthetic pressure covers candidate selection and final admission equally.
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python scripts/context-recovery-smoke/run.py @args
exit $LASTEXITCODE

$ErrorActionPreference = "Stop"
# home-smoke --home PATH --report FILE verifies Windows host ACL recovery without provisioning.
# lifecycle-probe --report NEW_FILE observes isolated host/RPC behavior, not sandbox readiness.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

& uv run python scripts/command-runtime/run.py @args
exit $LASTEXITCODE

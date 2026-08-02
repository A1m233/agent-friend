$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

if ($args.Count -gt 0 -and $args[0] -eq "agent-smoke") {
    [string[]]$agentSmokeArgs = if ($args.Count -gt 1) { @($args[1..($args.Count - 1)]) } else { @() }
    & uv run python scripts/social-platform-connectors/agent_smoke.py @agentSmokeArgs
    exit $LASTEXITCODE
}

if ($args.Count -gt 0 -and $args[0] -eq "quality") {
    [string[]]$qualityArgs = if ($args.Count -gt 1) { @($args[1..($args.Count - 1)]) } else { @() }
    & uv run python scripts/social-platform-connectors/quality.py @qualityArgs
    exit $LASTEXITCODE
}

& uv run python scripts/social-platform-connectors/run.py @args
exit $LASTEXITCODE

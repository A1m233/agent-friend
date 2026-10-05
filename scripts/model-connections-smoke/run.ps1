$ErrorActionPreference = "Stop"
# 151: capabilities uses tool images; documents only check reference projection.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
if ($args.Count -gt 0 -and $args[0] -eq "cpa") {
    & uv run python scripts/model-connections-smoke/cpa.py @($args | Select-Object -Skip 1)
} elseif ($args.Count -gt 0 -and $args[0] -eq "inspect") {
    & uv run python scripts/model-connections-smoke/inspection_smoke.py @($args | Select-Object -Skip 1)
} elseif ($args.Count -gt 0 -and $args[0] -eq "capabilities") {
    & uv run python scripts/model-connections-smoke/capability_smoke.py @($args | Select-Object -Skip 1)
} else {
    & uv run python scripts/model-connections-smoke/run.py @args
}
exit $LASTEXITCODE

# --custom: generic connection protocol; bounded synthetic compaction trigger, 4096 output tokens.

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
if ($args.Count -ge 2 -and $args[0] -eq "streaming") {
    pnpm --dir frontend exec node ../scripts/dev-session-mock/control.mjs $args[1]
    exit $LASTEXITCODE
}
uv run python scripts/dev-session-mock/run.py @args

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Set-Location (Join-Path $PSScriptRoot "..\..")

$Mode = if ($args.Count -gt 0) { $args[0] } else { "doctor" }
if ($Mode -ne "sync" -and $Mode -ne "doctor") {
    throw "Usage: .\scripts\codex-adapters\run.ps1 [sync|doctor]"
}

$Remaining = @()
if ($args.Count -gt 1) {
    $Remaining = $args[1..($args.Count - 1)]
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $Python) {
    throw "Python is required to run Codex adapter scripts. Install Python and retry."
}

if ($Mode -eq "sync") {
    & $Python.Source "$HOME/.cursor/scripts/codex-adapters.py" project-sync --root . @Remaining
    exit $LASTEXITCODE
}

& $Python.Source "$HOME/.cursor/scripts/codex-adapters.py" project-doctor --root . @Remaining
exit $LASTEXITCODE

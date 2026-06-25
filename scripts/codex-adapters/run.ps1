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

function Test-PythonExe {
    param([string]$Candidate)
    if (-not $Candidate) {
        return $false
    }
    & $Candidate --version *> $null
    return $LASTEXITCODE -eq 0
}

$PythonExe = $null
foreach ($Name in @("python", "python3")) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Command -and (Test-PythonExe $Command.Source)) {
        $PythonExe = $Command.Source
        break
    }
}
foreach ($Candidate in @(
    (Join-Path (Get-Location) ".venv\Scripts\python.exe"),
    (Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
)) {
    if (-not $PythonExe -and (Test-Path -LiteralPath $Candidate) -and (Test-PythonExe $Candidate)) {
        $PythonExe = $Candidate
    }
}
if (-not $PythonExe) {
    throw "Python is required to run Codex adapter scripts. Install Python and retry."
}

if ($Mode -eq "sync") {
    & $PythonExe "$HOME/.cursor/scripts/codex-adapters.py" project-sync --root . @Remaining
    exit $LASTEXITCODE
}

& $PythonExe "$HOME/.cursor/scripts/codex-adapters.py" project-doctor --root . @Remaining
exit $LASTEXITCODE

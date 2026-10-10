# Discover installed Python only. Never download, sync, or create an environment.
function Get-BuildPython {
    $candidates = @((Join-Path (Get-Location) '.venv\Scripts\python.exe'))
    foreach ($name in @('python3.12', 'python')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch 'WindowsApps') { $candidates += $command.Source }
    }
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $found = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $candidates += "$found" }
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            & $candidate -B -c 'import sys; sys.exit(sys.version_info[:2] != (3,12))' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    throw 'Python 3.12 is required. Install it explicitly; no build dependencies were installed.'
}

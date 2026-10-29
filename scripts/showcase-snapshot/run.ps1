$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Set-Location (Join-Path $PSScriptRoot "..\..")

# --verify-checkout scans without writes; --sync-checkout requires a clean checkout.
# Optional SHOWCASE_PRIVATE_USER / SHOWCASE_PRIVATE_DISPLAY_NAME configure personal redaction.
# Snapshots retain the root LICENSE and NOTICE alongside third-party license files.

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if ($Uv) {
    & $Uv.Source run --no-project --offline --no-python-downloads python "scripts/showcase-snapshot/snapshot.py" @args
    exit $LASTEXITCODE
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $Python) {
    throw "Python or uv is required to build the showcase snapshot."
}

& $Python.Source "scripts/showcase-snapshot/snapshot.py" @args
exit $LASTEXITCODE

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python -X utf8 -m tools.pokemon @args
# install/update/watch-desktop/verify-visual/ocr-check/ocr-serve/ocr-stop share Python.
# watch-desktop uses rotation-safe reads and reconciles logical calls at the terminal state.
# --run-id/--runtime-id pin a planned run; persistent API failure leaves an expired budget.
exit $LASTEXITCODE

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python -X utf8 -m tools.pokemon @args
# install/update/watch-desktop/verify-visual/ocr-check share the Python implementation.
# watch-desktop uses rotation-safe reads and reconciles logical calls at the terminal state.
exit $LASTEXITCODE

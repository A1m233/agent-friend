$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
# Runtime builds enforce dependency packaging review and frozen capability smokes.
uv run --group package --group memory-retrieval-benchmark python scripts/package/run.py @args
exit $LASTEXITCODE

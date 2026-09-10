$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
# Installer smoke requires normal Windows shell paths; AppData overrides are rejected.
# Runtime builds enforce Guide provenance, dependency review, and frozen capability smokes.
uv run --group package --group memory-retrieval-benchmark python scripts/package/run.py @args
exit $LASTEXITCODE

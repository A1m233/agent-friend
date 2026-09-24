$ErrorActionPreference = "Stop"
# Runtime doctor includes offline PNG/JPEG/WebP decoding and snapshot verification.
# It also verifies bounded sandbox recovery and unknown-start no-replay behavior.
# Session uploads are verified, copied, reopened, and cleaned offline by runtime doctor.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
# Installer smoke requires normal Windows shell paths; AppData overrides are rejected.
# Runtime builds enforce Guide provenance, companion hashes, and non-mutating helper load smoke.
uv run --group package --group memory-retrieval-benchmark python scripts/package/run.py @args
exit $LASTEXITCODE

$ErrorActionPreference = "Stop"
# Runtime doctor includes offline PNG/JPEG/WebP decoding and snapshot verification.
# It also verifies bounded sandbox recovery and unknown-start no-replay behavior.
# Session uploads are verified, copied, reopened, and cleaned offline by runtime doctor.
# Scheduled inputs verify fixed targets and durable request interruption without model calls.
# QQ approval smoke uses local transport only; real client acceptance remains separate.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
# Package writes preflight shell paths and relay through same-user Explorer if redirected.
# The normal host repeats all checks; AppData overrides remain rejected.
# Runtime builds enforce Guide provenance, companion hashes, and non-mutating helper load smoke.
. (Join-Path $PSScriptRoot "..\_lib\python312.ps1")
$python = Get-BuildPython
& $python -B (Join-Path $PSScriptRoot "dispatch.py") @args
exit $LASTEXITCODE

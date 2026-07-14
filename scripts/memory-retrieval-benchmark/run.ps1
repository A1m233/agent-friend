$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

uv run --group memory-retrieval-benchmark python -m memory_eval.retrieval_benchmark @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

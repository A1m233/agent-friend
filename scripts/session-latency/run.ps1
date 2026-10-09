$ErrorActionPreference = "Stop"
# prepare/scroll/lock-compare observe; real/isolated serve/long-runtime require separate LLM authorization.
# offline sends on disposable copies, blocks network, and reports derived storage costs.
# cold-cache compares fresh/retained derived caches on disposable copies; no provider calls.
# desktop-fixture --synthetic-only prepares 2/65-turn histories, full bodies and oldest-message references; never launches.
# desktop-history checks original UI, complete turn pages, grouped tools and reference paging; never sends.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/session-latency/run.py @args
exit $LASTEXITCODE

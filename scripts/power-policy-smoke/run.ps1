$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# Windows-only true-device smoke. macOS true-device validation is outside requirement 057.
Set-Location (Join-Path $PSScriptRoot "..\..")

if (-not $IsWindows -and $env:OS -ne "Windows_NT") {
    throw "power-policy-smoke is Windows-only"
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo is required"
}
if (-not (Get-Command powercfg.exe -ErrorAction SilentlyContinue)) {
    throw "powercfg.exe is required"
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "power-policy-smoke requires an elevated PowerShell terminal because powercfg /requests requires administrator privileges"
}

$stdoutPath = [System.IO.Path]::GetTempFileName()
$stderrPath = [System.IO.Path]::GetTempFileName()
$process = $null
$env:AGENT_FRIEND_POWER_SMOKE_SECONDS = "15"
$arguments = @(
    "test",
    "--manifest-path", "frontend/src-tauri/Cargo.toml",
    "power_request_smoke_holds_until_timeout",
    "--",
    "--ignored",
    "--nocapture"
)

try {
    $process = Start-Process -FilePath "cargo" -ArgumentList $arguments -PassThru `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $active = $false
    for ($attempt = 0; $attempt -lt 360; $attempt++) {
        Start-Sleep -Milliseconds 500
        $stdoutText = Get-Content -Raw -Encoding UTF8 $stdoutPath
        if ($stdoutText -match "AGENT_FRIEND_POWER_SMOKE_ACTIVE") {
            $active = $true
            break
        }
        if ($process.HasExited) { break }
    }
    if (-not $active) {
        $stderrText = Get-Content -Raw -Encoding UTF8 $stderrPath
        throw "Rust smoke did not establish the power request: $stderrText"
    }

    $requests = powercfg.exe /requests | Out-String
    if ($requests -notmatch "agent-friend power policy smoke") {
        throw "Windows did not expose the agent-friend power request via powercfg /requests"
    }

    $process.WaitForExit()
    $process.Refresh()
    $stdoutText = Get-Content -Raw -Encoding UTF8 $stdoutPath
    $exitCode = $process.ExitCode
    $testPassed = $stdoutText -match "test result: ok\. 1 passed; 0 failed"
    if (($null -ne $exitCode -and $exitCode -ne 0) -or -not $testPassed) {
        $stderrText = Get-Content -Raw -Encoding UTF8 $stderrPath
        throw "Rust smoke process failed.`nstdout:`n$stdoutText`nstderr:`n$stderrText"
    }

    $after = powercfg.exe /requests | Out-String
    if ($after -match "agent-friend power policy smoke") {
        throw "agent-friend power request remained after inhibitor release"
    }
    Write-Host "power-policy-smoke: acquire visible, display request absent by API design, release clean"
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        $process.WaitForExit()
    }
    Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    Remove-Item Env:AGENT_FRIEND_POWER_SMOKE_SECONDS -ErrorAction SilentlyContinue
}

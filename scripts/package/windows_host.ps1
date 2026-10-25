param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Worker,
    [Parameter(Mandatory=$true)][string]$Request
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
# Use Explorer's desktop view, not a Shell.Application object hosted by this process.
# https://devblogs.microsoft.com/oldnewthing/20131118-00/?p=2643
$hwnd = 0
$desktop = (New-Object -ComObject Shell.Application).Windows().FindWindowSW(0, 0, 8, [ref]$hwnd, 1)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PackageDesktopIdentity {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
}
'@
[uint32]$shellProcessId = 0
[void][PackageDesktopIdentity]::GetWindowThreadProcessId([IntPtr]$desktop.HWND, [ref]$shellProcessId)
$shellProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$shellProcessId"
$owner = Invoke-CimMethod -InputObject $shellProcess -MethodName GetOwnerSid
$current = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne $current -or
    (Get-Process -Id $shellProcessId).SessionId -ne (Get-Process -Id $PID).SessionId) {
    throw 'Package host requires Explorer in the same user and desktop session.'
}
foreach ($path in @($Python, $Worker, $Request)) {
    if (-not [IO.Path]::IsPathRooted($path) -or $path.Contains('"') -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'Package host requires existing absolute file paths.'
    }
}
$arguments = '"{0}" --worker "{1}"' -f $Worker, $Request
$desktop.Document.Application.ShellExecute($Python, $arguments, (Split-Path -Parent $Worker), 'open', 0)

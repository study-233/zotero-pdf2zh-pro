Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$windowsDir = Join-Path $PSScriptRoot "windows"
$failed = $false
$powerShellFiles = @(Get-ChildItem -LiteralPath $windowsDir -Filter "*.ps1")
$powerShellFiles += Get-Item -LiteralPath (Join-Path $PSScriptRoot "test_windows_lifecycle.ps1")
$powerShellFiles | ForEach-Object {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $_.FullName,
        [ref]$tokens,
        [ref]$errors
    ) | Out-Null
    if ($errors) {
        $failed = $true
        $errors | ForEach-Object {
            Write-Error "$($_.Extent.File): $($_.Message)"
        }
    } else {
        Write-Host "$($_.Name): Windows PowerShell syntax OK"
    }
}

$forbiddenPatterns = @(
    "Register-ScheduledTask",
    "New-ScheduledTask",
    "schtasks.exe",
    "schtasks ",
    "-AtLogOn",
    "New-Service",
    "sc.exe create",
    "New-NetFirewallRule",
    "Set-NetFirewallProfile",
    "netsh advfirewall"
)
$scriptText = Get-ChildItem -LiteralPath $windowsDir -File |
    ForEach-Object { Get-Content -Raw -LiteralPath $_.FullName }
foreach ($pattern in $forbiddenPatterns) {
    if ($scriptText -match [regex]::Escape($pattern)) {
        Write-Error "Windows package contains forbidden system integration: $pattern"
        $failed = $true
    }
}

$commonText = Get-Content -Raw -LiteralPath (Join-Path $windowsDir "common.ps1")
$expectedAutostart = '$AutostartRunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"'
if ($commonText.IndexOf($expectedAutostart, [StringComparison]::Ordinal) -lt 0) {
    Write-Error "The only allowed autostart mechanism is the current-user HKCU Run value."
    $failed = $true
}

if ($failed) {
    exit 1
}

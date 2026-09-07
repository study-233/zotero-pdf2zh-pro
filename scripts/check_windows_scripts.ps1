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

$requiredLocationControls = @(
    "PDF2ZH_WINDOWS_REGISTRY_KEY",
    "InstallRoot",
    "UV_INSTALL_DIR",
    "UV_TOOL_DIR",
    "UV_TOOL_BIN_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "UV_CACHE_DIR"
)
foreach ($control in $requiredLocationControls) {
    if ($commonText.IndexOf($control, [StringComparison]::Ordinal) -lt 0) {
        Write-Error "Windows path configuration is missing: $control"
        $failed = $true
    }
}

$relocateText = Get-Content -Raw -LiteralPath (Join-Path $windowsDir "relocate.ps1")
foreach ($stage in @("stop-server.ps1", "start-server.ps1", "Save-InstallRoot", "last-operation-error.txt", "CurrentVersion")) {
    if ($relocateText.IndexOf($stage, [StringComparison]::Ordinal) -lt 0) {
        Write-Error "Windows relocation rollback contract is missing: $stage"
        $failed = $true
    }
}

if ($failed) {
    exit 1
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$windowsDir = Join-Path $PSScriptRoot "windows"
$failed = $false
Get-ChildItem -LiteralPath $windowsDir -Filter "*.ps1" | ForEach-Object {
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
    "-AtLogOn"
)
$scriptText = Get-ChildItem -LiteralPath $windowsDir -File |
    ForEach-Object { Get-Content -Raw -LiteralPath $_.FullName }
foreach ($pattern in $forbiddenPatterns) {
    if ($scriptText -match [regex]::Escape($pattern)) {
        Write-Error "Windows package contains forbidden auto-start behavior: $pattern"
        $failed = $true
    }
}

if ($failed) {
    exit 1
}

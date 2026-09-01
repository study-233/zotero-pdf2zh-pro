param(
    [Parameter(Mandatory = $true)][int]$ParentProcessId,
    [string]$PackageSource
)

. (Join-Path $PSScriptRoot "common.ps1")
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-UpdateLog {
    param([string]$Message)
    New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $ControlLogFile -Value "$timestamp [update] $Message" -Encoding utf8
}

function Start-Cleanup {
    param([string]$Target)
    $cleanupFile = Join-Path ([IO.Path]::GetTempPath()) (
        "zotero-pdf2zh-pro-update-cleanup-{0}.ps1" -f [guid]::NewGuid().ToString("N")
    )
    $cleanup = @'
param([string]$TargetPath)
Start-Sleep -Seconds 5
Remove-Item -LiteralPath $TargetPath -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
'@
    Set-Content -LiteralPath $cleanupFile -Value $cleanup -Encoding utf8
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -TargetPath "{1}"' -f $cleanupFile, $Target
    Start-Process -FilePath $PSHOME\powershell.exe -ArgumentList $arguments -WindowStyle Hidden
}

$stagingRoot = Split-Path $PSScriptRoot -Parent
$installScript = Join-Path $PSScriptRoot "install.ps1"
$guiSource = Join-Path $PSScriptRoot "$ProductName.exe"

try {
    Write-UpdateLog "Waiting for control center process $ParentProcessId to exit."
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        if (-not (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) {
        throw "The previous control center did not exit within 20 seconds."
    }

    Write-UpdateLog "Applying Windows package $PackageVersion."
    $installArguments = @{
        GuiSource = $guiSource
        NonInteractive = $true
    }
    if ($PackageSource) {
        $installArguments.PackageSource = [IO.Path]::GetFullPath($PackageSource)
    }
    & $installScript @installArguments
    Write-UpdateLog "Update installed successfully; starting the new control center."
    Start-Process -FilePath $ControlPanelExecutable -ArgumentList "--post-install" -WindowStyle Hidden
    Start-Cleanup -Target $stagingRoot
    exit 0
} catch {
    Write-UpdateLog "Update failed: $($_.Exception.Message)"
    if (Test-Path -LiteralPath $ControlPanelExecutable -PathType Leaf) {
        try {
            Start-Process -FilePath $ControlPanelExecutable -ArgumentList "--post-install" -WindowStyle Hidden
            Write-UpdateLog "Restarted the previous control center."
        } catch {
            Write-UpdateLog "Could not restart the previous control center: $($_.Exception.Message)"
        }
    }
    Start-Cleanup -Target $stagingRoot
    exit 1
}

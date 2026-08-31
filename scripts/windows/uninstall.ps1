param(
    [switch]$PurgeData,
    [switch]$NonInteractive
)

. (Join-Path $PSScriptRoot "common.ps1")
Assert-WindowsX64

foreach ($registryPath in @($AutostartRunKey, $AutostartApprovedKey)) {
    if (Test-Path -LiteralPath $registryPath) {
        Remove-ItemProperty -LiteralPath $registryPath -Name $ProductName -Force -ErrorAction SilentlyContinue
    }
}

try {
    & (Join-Path $PSScriptRoot "stop-server.ps1") -Quiet
} catch {
    Write-Warning $_
    throw "Uninstall stopped because the managed server could not be safely stopped."
}

try {
    Stop-ManagedControlPanel
} catch {
    Write-Warning $_
    throw "Uninstall stopped because the managed control center could not be safely stopped."
}

$uv = Get-UvExecutable
if ($uv) {
    & $uv tool uninstall $ProductName
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "uv tool uninstall returned exit code $LASTEXITCODE."
    }
} else {
    Write-Warning "uv.exe was not found; the Python tool may need manual removal."
}

Remove-Item -LiteralPath $StartMenuDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-ManagedProcessState
Remove-Item -LiteralPath $ExecutableFile -Force -ErrorAction SilentlyContinue
Remove-ManagedControlPanelState
Remove-Item -LiteralPath $InstalledVersionFile -Force -ErrorAction SilentlyContinue

if (-not $PurgeData -and -not $NonInteractive) {
    try {
        Add-Type -AssemblyName PresentationFramework
        $message = "Delete all task data, translated PDFs, and logs?" +
            [Environment]::NewLine + [Environment]::NewLine +
            "Choose No to keep them for a future reinstall."
        $answer = [System.Windows.MessageBox]::Show(
            $message,
            "Uninstall zotero-pdf2zh-pro",
            [System.Windows.MessageBoxButton]::YesNo,
            [System.Windows.MessageBoxImage]::Question,
            [System.Windows.MessageBoxResult]::No
        )
        $PurgeData = $answer -eq [System.Windows.MessageBoxResult]::Yes
    } catch {
        $answer = Read-Host "Delete task data and logs? Type DELETE to confirm"
        $PurgeData = $answer -ceq "DELETE"
    }
}

$cleanupTarget = if ($PurgeData) { $AppRoot } else { $BinDir }
$cleanupFile = Join-Path ([IO.Path]::GetTempPath()) (
    "zotero-pdf2zh-pro-cleanup-{0}.ps1" -f [guid]::NewGuid().ToString("N")
)
$cleanup = @'
param([string]$TargetPath)
Start-Sleep -Seconds 2
Remove-Item -LiteralPath $TargetPath -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $TargetPath) {
    $reportPath = Join-Path ([IO.Path]::GetTempPath()) "zotero-pdf2zh-pro-uninstall-error.txt"
    $remaining = Get-ChildItem -LiteralPath $TargetPath -Recurse -Force -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    @(
        "Some files could not be removed."
        "Remove this directory manually after closing programs that use it:"
        $TargetPath
        ""
        $remaining
    ) | Set-Content -LiteralPath $reportPath -Encoding utf8
    Start-Process -FilePath "notepad.exe" -ArgumentList ('"{0}"' -f $reportPath)
}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
'@
Set-Content -LiteralPath $cleanupFile -Value $cleanup -Encoding utf8
$cleanupArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -TargetPath "{1}"' -f $cleanupFile, $cleanupTarget
Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $cleanupArguments -WindowStyle Hidden

if ($PurgeData) {
    Write-Host "Uninstall complete. Program files, task data, results, and logs will be removed."
} else {
    Write-Host "Uninstall complete. Task data and logs were preserved in $AppRoot"
}
Write-Host "uv was left installed because other applications may use it."

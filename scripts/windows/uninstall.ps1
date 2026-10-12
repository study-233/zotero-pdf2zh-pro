param(
    [switch]$PurgeData,
    [switch]$NonInteractive
)

. (Join-Path $PSScriptRoot "common.ps1")
. (Join-Path $PSScriptRoot "runtime.ps1")
Assert-WindowsX64
$operation=Enter-RuntimeOperation
Request-SafeMaintenance | Out-Null

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

$usingPrivateUv = Test-Path -LiteralPath $PrivateUvExecutable -PathType Leaf
$uv = Get-UvExecutable
if ($uv -and -not (Test-Path -LiteralPath (Join-Path $RuntimeDir 'versions'))) {
    & $uv tool uninstall $ProductName
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "uv tool uninstall returned exit code $LASTEXITCODE."
    }
} elseif (-not $uv) {
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

if ($PurgeData) {
    Remove-InstallRoot
}
$cleanupFile = Join-Path ([IO.Path]::GetTempPath()) (
    "zotero-pdf2zh-pro-cleanup-{0}.ps1" -f [guid]::NewGuid().ToString("N")
)
$cleanup = @'
param([string]$RootPath, [switch]$PurgeData, [switch]$NonInteractive)
$ErrorActionPreference='Stop'
Start-Sleep -Seconds 2
$fullRoot=[IO.Path]::GetFullPath($RootPath).TrimEnd('\')
if ($fullRoot -eq [IO.Path]::GetPathRoot($fullRoot).TrimEnd('\') -or ((Get-Item -LiteralPath $fullRoot).Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw 'Unsafe uninstall root.'}
$cleanupLock=[IO.File]::Open((Join-Path $fullRoot '.operation.lock'),'OpenOrCreate','ReadWrite','None')
try {
$targets = if ($PurgeData) {
    @(Get-ChildItem -LiteralPath $fullRoot -Force|Where-Object {$_.Name -ne '.operation.lock'}|Select-Object -ExpandProperty FullName)
} else {
    @(
        (Join-Path $RootPath "bin"),
        (Join-Path $RootPath "cache"),
        (Join-Path $RootPath "runtime"),
        (Join-Path $RootPath "updates"),
        (Join-Path $RootPath "installed-version.txt"),
        (Join-Path $RootPath "server-executable.txt"),
        (Join-Path $RootPath "server.pid"),
        (Join-Path $RootPath "control-panel-executable.txt"),
        (Join-Path $RootPath "control-panel.pid"),
        (Join-Path $RootPath "last-operation-error.txt")
        (Join-Path $RootPath "install-state.json")
    )
}
foreach ($target in $targets) {
    $fullTarget=[IO.Path]::GetFullPath($target)
    $fullRoot=[IO.Path]::GetFullPath($RootPath).TrimEnd('\')
    if ($fullTarget -ne $fullRoot -and -not $fullTarget.StartsWith($fullRoot+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'Unsafe uninstall cleanup path.'}
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
}
} finally {$cleanupLock.Dispose()}
if ($PurgeData) {
    Remove-Item -LiteralPath (Join-Path $fullRoot '.operation.lock') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $fullRoot -Force -ErrorAction SilentlyContinue
}
$remainingTargets = @($targets | Where-Object { Test-Path -LiteralPath $_ })
if ($remainingTargets.Count -gt 0) {
    $reportPath = Join-Path ([IO.Path]::GetTempPath()) "zotero-pdf2zh-pro-uninstall-error.txt"
    $remaining = $remainingTargets | ForEach-Object {
        if (Test-Path -LiteralPath $_ -PathType Container) {
            Get-ChildItem -LiteralPath $_ -Recurse -Force -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
        } else {
            $_
        }
    }
    @(
        "Some files could not be removed."
        "Remove these paths manually after closing programs that use them:"
        $remainingTargets
        ""
        $remaining
    ) | Set-Content -LiteralPath $reportPath -Encoding utf8
    if (-not $NonInteractive) {Start-Process -FilePath "notepad.exe" -ArgumentList ('"{0}"' -f $reportPath)}
}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
'@
Set-Content -LiteralPath $cleanupFile -Value $cleanup -Encoding utf8
$cleanupArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -RootPath "{1}"' -f $cleanupFile, $AppRoot
if ($PurgeData) {
    $cleanupArguments += " -PurgeData"
}
if ($NonInteractive) {$cleanupArguments += ' -NonInteractive'}
Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $cleanupArguments -WindowStyle Hidden
$operation.Dispose()

if ($PurgeData) {
    Write-Host "Uninstall complete. Program files, task data, results, and logs will be removed."
} else {
    Write-Host "Uninstall complete. Task data and logs were preserved in $AppRoot"
}
if ($usingPrivateUv) {
    Write-Host "The private uv and Python runtime will be removed."
} else {
    Write-Host "Shared uv and Python files were left installed because other applications may use them."
}

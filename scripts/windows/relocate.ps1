param(
    [Parameter(Mandatory = $true)][int]$ParentProcessId,
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$DestinationRoot,
    [Parameter(Mandatory = $true)][string]$CurrentVersion,
    [string]$PackageSource,
    [switch]$SkipUvBootstrap
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$DestinationRoot = [IO.Path]::GetFullPath($DestinationRoot)
$env:PDF2ZH_WINDOWS_APP_ROOT = $SourceRoot
. (Join-Path $PSScriptRoot "common.ps1")
Assert-WindowsX64

$sourcePaths = @{
    Root = $SourceRoot
    Bin = Join-Path $SourceRoot "bin"
    Data = Join-Path $SourceRoot "data"
    Logs = Join-Path $SourceRoot "logs"
    ControlLog = Join-Path (Join-Path $SourceRoot "logs") "control-panel.log"
    Gui = Join-Path (Join-Path $SourceRoot "bin") "$ProductName.exe"
    Uv = Get-UvExecutable
    Server = Get-ServerExecutable
}
$sourceRootKey = $SourceRoot.TrimEnd("\").ToLowerInvariant()
$legacyToolOutsideRoot = $sourcePaths.Server -and -not (
    [IO.Path]::GetFullPath($sourcePaths.Server).ToLowerInvariant().StartsWith("$sourceRootKey\")
)
$sourcePrivateRuntime = Test-Path -LiteralPath (Join-Path $SourceRoot "runtime\uv\uv.exe") -PathType Leaf
$sourceVersionFile = Join-Path $SourceRoot "installed-version.txt"
if (-not (Test-Path -LiteralPath $sourceVersionFile -PathType Leaf)) {
    throw "The source installation does not have a version marker."
}
$sourceVersion = (Get-Content -Raw -LiteralPath $sourceVersionFile).Trim()
if ($sourceVersion -ne $CurrentVersion -or $PackageVersion -ne $CurrentVersion) {
    throw "The control center, source installation, and migration package versions must match: control=$CurrentVersion, source=$sourceVersion, package=$PackageVersion."
}
$sourceWasRunning = $null -ne (Get-ListeningProcessId)
$savedRootBeforeMigration = Get-SavedInstallRoot
$autostartValue = $null
if (Test-Path -LiteralPath $AutostartRunKey) {
    try {
        $autostartValue = Get-ItemPropertyValue -LiteralPath $AutostartRunKey -Name $ProductName -ErrorAction Stop
    } catch {
        $autostartValue = $null
    }
}
$autostartEnabled = $null -ne $autostartValue
$destinationCreated = $false
$targetInstalled = $false

function Write-RelocationLog {
    param([string]$Message)
    New-Item -ItemType Directory -Force -Path $sourcePaths.Logs | Out-Null
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $sourcePaths.ControlLog -Value "$timestamp [relocate] $Message" -Encoding utf8
    Write-Host $Message
}

function Write-RelocationErrorDetails {
    param(
        [string]$Operation,
        [Management.Automation.ErrorRecord]$ErrorRecord
    )
    $exception = $ErrorRecord.Exception
    $nativeErrorCode = Get-WindowsNativeErrorCode -Exception $exception
    $nativeText = if ($null -eq $nativeErrorCode) { "none" } else { [string]$nativeErrorCode }
    $hresult = [BitConverter]::ToUInt32([BitConverter]::GetBytes([int]$exception.HResult), 0).ToString("X8")
    Write-RelocationLog (
        "$Operation failed: type=$($exception.GetType().FullName); nativeErrorCode=$nativeText; " +
        "hresult=0x$hresult; fullyQualifiedErrorId=$($ErrorRecord.FullyQualifiedErrorId); message=$($exception.Message)"
    )
    if ($ErrorRecord.ScriptStackTrace) {
        $stack = $ErrorRecord.ScriptStackTrace -replace "[\r\n]+", " | "
        Write-RelocationLog "$Operation stack: $stack"
    }
}

function Invoke-RelocationInteropOperation {
    param([string]$Name, [scriptblock]$Action)
    Write-RelocationLog "$Name started."
    try {
        Invoke-WindowsInteropOperation -Action $Action -OnRetry {
            param($completedAttempt, $nextAttempt, $delay, $errorRecord)
            Write-RelocationLog (
                "$Name returned Windows error 122 on attempt $completedAttempt; " +
                "retrying with attempt $nextAttempt after ${delay}ms."
            )
        }
        Write-RelocationLog "$Name completed."
    } catch {
        Write-RelocationErrorDetails -Operation $Name -ErrorRecord $_
        throw
    }
}

function Set-RootEnvironment {
    param([string]$Root, [bool]$PrivateRuntime)
    $env:PDF2ZH_WINDOWS_APP_ROOT = $Root
    if ($PrivateRuntime) {
        $env:UV_INSTALL_DIR = Join-Path $Root "runtime\uv"
        $env:UV_TOOL_DIR = Join-Path $Root "runtime\tools"
        $env:UV_TOOL_BIN_DIR = Join-Path $Root "runtime\tool-bin"
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $Root "runtime\python"
        $env:UV_CACHE_DIR = Join-Path $Root "cache"
    } else {
        foreach ($name in @("UV_INSTALL_DIR", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR")) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
    }
}

function Set-ProductShortcuts {
    param([string]$Root)
    New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
    Get-ChildItem -LiteralPath $StartMenuDir -Filter "*.lnk" -ErrorAction SilentlyContinue | Remove-Item -Force
    $shell = New-Object -ComObject WScript.Shell
    $gui = Join-Path (Join-Path $Root "bin") "$ProductName.exe"
    $controlShortcut = $shell.CreateShortcut((Join-Path $StartMenuDir "$ProductName.lnk"))
    $controlShortcut.TargetPath = $gui
    $controlShortcut.WorkingDirectory = $Root
    $controlShortcut.Save()
    $uninstallShortcut = $shell.CreateShortcut((Join-Path $StartMenuDir "卸载.lnk"))
    $uninstallShortcut.TargetPath = Join-Path (Join-Path $Root "bin") "uninstall.cmd"
    $uninstallShortcut.WorkingDirectory = $Root
    $uninstallShortcut.Save()
}

function Set-ProductAutostart {
    param([string]$Root, [bool]$Enabled)
    if ($Enabled) {
        New-Item -Force -Path $AutostartRunKey | Out-Null
        $gui = Join-Path (Join-Path $Root "bin") "$ProductName.exe"
        Set-ItemProperty -LiteralPath $AutostartRunKey -Name $ProductName -Value ('"{0}" --autostart' -f $gui)
    } else {
        Remove-ItemProperty -LiteralPath $AutostartRunKey -Name $ProductName -Force -ErrorAction SilentlyContinue
    }
}

function Assert-RelocationDestination {
    if (-not [IO.Path]::IsPathRooted($DestinationRoot)) {
        throw "The destination must be an absolute path."
    }
    $sourceKey = $SourceRoot.TrimEnd("\").ToLowerInvariant()
    $destinationKey = $DestinationRoot.TrimEnd("\").ToLowerInvariant()
    if (
        $sourceKey -eq $destinationKey -or
        $destinationKey.StartsWith("$sourceKey\") -or
        $sourceKey.StartsWith("$destinationKey\")
    ) {
        throw "The source and destination cannot be equal or nested."
    }
    if (Test-Path -LiteralPath $DestinationRoot -PathType Leaf) {
        throw "The destination points to a file: $DestinationRoot"
    }
    if (Test-Path -LiteralPath $DestinationRoot -PathType Container) {
        $existing = @(Get-ChildItem -Force -LiteralPath $DestinationRoot)
        if ($existing.Count -gt 0) {
            throw "The destination is not empty: $DestinationRoot"
        }
        $script:destinationCreated = $true
    } else {
        New-Item -ItemType Directory -Force -Path $DestinationRoot | Out-Null
        $script:destinationCreated = $true
    }
    $probe = Join-Path $DestinationRoot (".write-test-{0}" -f [guid]::NewGuid().ToString("N"))
    try {
        Set-Content -LiteralPath $probe -Value "ok" -Encoding ascii
    } finally {
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    }
}

function Copy-DirectoryContents {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    if (Test-Path -LiteralPath $Source -PathType Container) {
        Get-ChildItem -Force -LiteralPath $Source | Copy-Item -Destination $Destination -Recurse -Force
    }
}

function Start-SourceControlCenter {
    Set-RootEnvironment -Root $SourceRoot -PrivateRuntime $sourcePrivateRuntime
    if ($sourceWasRunning) {
        Start-Process -FilePath $sourcePaths.Gui -ArgumentList "--post-install" -WindowStyle Hidden
    } else {
        Start-Process -FilePath $sourcePaths.Gui -WindowStyle Hidden
    }
}

function Start-SourceCleanup {
    $cleanupFile = Join-Path ([IO.Path]::GetTempPath()) (
        "zotero-pdf2zh-pro-relocate-cleanup-{0}.ps1" -f [guid]::NewGuid().ToString("N")
    )
    $warningFile = Join-Path $DestinationRoot "last-operation-error.txt"
    $cleanup = @'
param([string]$SourcePath, [string]$WarningPath)
Start-Sleep -Seconds 4
Remove-Item -LiteralPath $SourcePath -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $SourcePath) {
    Set-Content -LiteralPath $WarningPath -Value "The new installation is active, but the old directory could not be removed: $SourcePath" -Encoding utf8
}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
'@
    Set-Content -LiteralPath $cleanupFile -Value $cleanup -Encoding utf8
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -SourcePath "{1}" -WarningPath "{2}"' -f $cleanupFile, $SourceRoot, $warningFile
    Start-Process -FilePath $PSHOME\powershell.exe -ArgumentList $arguments -WindowStyle Hidden
}

try {
    Assert-RelocationDestination
    Write-RelocationLog "[1/8] Waiting for the control center to exit..."
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        if (-not (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) {
        throw "The control center did not exit within 20 seconds."
    }

    Write-RelocationLog "[2/8] Stopping the translation service..."
    & (Join-Path $sourcePaths.Bin "stop-server.ps1") -Quiet
    Remove-ManagedControlPanelState

    Write-RelocationLog "[3/8] Copying task data, recovery checkpoints, logs, and cache..."
    Copy-DirectoryContents -Source $sourcePaths.Data -Destination (Join-Path $DestinationRoot "data")
    Copy-DirectoryContents -Source $sourcePaths.Logs -Destination (Join-Path $DestinationRoot "logs")
    Copy-DirectoryContents -Source (Join-Path $SourceRoot "cache") -Destination (Join-Path $DestinationRoot "cache")

    Write-RelocationLog "[4/8] Installing the private uv and Python runtime..."
    $installArguments = @{
        GuiSource = $sourcePaths.Gui
        InstallRoot = $DestinationRoot
        DeferLocationCommit = $true
        AllowPreparedDestination = $true
        NoShortcuts = $true
        NonInteractive = $true
    }
    if ($PackageSource) {
        $installArguments.PackageSource = [IO.Path]::GetFullPath($PackageSource)
    }
    if ($SkipUvBootstrap) {
        $installArguments.SkipUvBootstrap = $true
    }
    & (Join-Path $sourcePaths.Bin "install.ps1") @installArguments
    $targetInstalled = $true
    $targetVersion = (Get-Content -Raw -LiteralPath (Join-Path $DestinationRoot "installed-version.txt")).Trim()
    if ($targetVersion -ne $CurrentVersion) {
        throw "The relocated installation version is $targetVersion; expected $CurrentVersion."
    }

    Write-RelocationLog "[5/8] Verifying the service from the new location..."
    Set-RootEnvironment -Root $DestinationRoot -PrivateRuntime $true
    & (Join-Path (Join-Path $DestinationRoot "bin") "start-server.ps1") -Quiet
    & (Join-Path (Join-Path $DestinationRoot "bin") "stop-server.ps1") -Quiet

    Write-RelocationLog "[6/8] Switching the saved installation location..."
    Invoke-RelocationInteropOperation -Name "Save target installation location" -Action {
        Save-InstallRoot -Path $DestinationRoot
    }
    Invoke-RelocationInteropOperation -Name "Create target Start menu shortcuts" -Action {
        Set-ProductShortcuts -Root $DestinationRoot
    }
    Invoke-RelocationInteropOperation -Name "Update target autostart registration" -Action {
        Set-ProductAutostart -Root $DestinationRoot -Enabled $autostartEnabled
    }

    Write-RelocationLog "[7/8] Starting the control center from $DestinationRoot..."
    Set-RootEnvironment -Root $DestinationRoot -PrivateRuntime $true
    Remove-Item -LiteralPath (Join-Path $DestinationRoot "last-operation-error.txt") -Force -ErrorAction SilentlyContinue
    $targetGui = Join-Path (Join-Path $DestinationRoot "bin") "$ProductName.exe"
    if ($sourceWasRunning) {
        Start-Process -FilePath $targetGui -ArgumentList "--post-install" -WindowStyle Hidden
    } else {
        Start-Process -FilePath $targetGui -WindowStyle Hidden
    }
    Write-RelocationLog "[8/8] Cleaning the old installation..."
    try {
        if ($sourcePaths.Uv -and $legacyToolOutsideRoot) {
            Set-RootEnvironment -Root $SourceRoot -PrivateRuntime $sourcePrivateRuntime
            & $sourcePaths.Uv tool uninstall $ProductName
            if ($LASTEXITCODE -ne 0) {
                Write-RelocationLog "The old product tool could not be removed automatically."
            }
        }
        Set-RootEnvironment -Root $DestinationRoot -PrivateRuntime $true
        Start-SourceCleanup
    } catch {
        Set-Content -LiteralPath (Join-Path $DestinationRoot "last-operation-error.txt") `
            -Value "The new installation is active, but the old installation requires manual cleanup: $SourceRoot" `
            -Encoding utf8
    }
    exit 0
} catch {
    $relocationError = $_
    $failure = "Installation relocation failed: $($relocationError.Exception.Message)"
    Write-RelocationLog $failure
    Write-RelocationErrorDetails -Operation "Installation relocation" -ErrorRecord $relocationError
    try {
        if ($targetInstalled) {
            Set-RootEnvironment -Root $DestinationRoot -PrivateRuntime $true
            & (Join-Path (Join-Path $DestinationRoot "bin") "stop-server.ps1") -Quiet
        }
    } catch {
        Write-RelocationLog "The incomplete destination service could not be stopped cleanly."
        Write-RelocationErrorDetails -Operation "Stop incomplete destination service" -ErrorRecord $_
    }
    try {
        Invoke-RelocationInteropOperation -Name "Restore source installation location" -Action {
            if ($savedRootBeforeMigration) {
                Save-InstallRoot -Path $savedRootBeforeMigration
            } else {
                Remove-InstallRoot
            }
        }
    } catch {
        Write-RelocationLog "Rollback is continuing after the saved installation location could not be restored."
    }
    try {
        Invoke-RelocationInteropOperation -Name "Restore source Start menu shortcuts" -Action {
            Set-ProductShortcuts -Root $SourceRoot
        }
    } catch {
        Write-RelocationLog "Rollback is continuing after the source shortcuts could not be restored."
    }
    try {
        Invoke-RelocationInteropOperation -Name "Restore source autostart registration" -Action {
            Set-ProductAutostart -Root $SourceRoot -Enabled $autostartEnabled
        }
    } catch {
        Write-RelocationLog "Rollback is continuing after the source autostart registration could not be restored."
    }
    try {
        if ($destinationCreated -or $targetInstalled) {
            Remove-Item -LiteralPath $DestinationRoot -Recurse -Force -ErrorAction Stop
            if (Test-Path -LiteralPath $DestinationRoot) {
                Write-RelocationLog "The incomplete destination could not be removed completely: $DestinationRoot"
            }
        }
    } catch {
        Write-RelocationLog "The incomplete destination could not be removed completely: $DestinationRoot"
        Write-RelocationErrorDetails -Operation "Remove incomplete destination" -ErrorRecord $_
    }
    try {
        Set-Content -LiteralPath (Join-Path $SourceRoot "last-operation-error.txt") -Value $failure -Encoding utf8
    } catch {
        Write-RelocationLog "The relocation failure file could not be written."
    }
    try {
        Start-SourceControlCenter
    } catch {
        Write-RelocationLog "The original control center could not be restarted automatically."
        Write-RelocationErrorDetails -Operation "Restart source control center" -ErrorRecord $_
    }
    exit 1
}

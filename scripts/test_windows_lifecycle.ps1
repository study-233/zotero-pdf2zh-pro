param(
    [Parameter(Mandatory = $true)][string]$GuiBinary,
    [Parameter(Mandatory = $true)][string]$PackageSource
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$windowsDir = Join-Path $PSScriptRoot "windows"
$gui = [IO.Path]::GetFullPath($GuiBinary)
$package = [IO.Path]::GetFullPath($PackageSource)
. (Join-Path $windowsDir "common.ps1")

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Wait-ExpectedHealth {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $health = Get-ServerHealth
        if (Test-ExpectedHealth -Health $health) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "The expected health endpoint did not become ready."
}

function Wait-ControlPanel {
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $controlProcessId = Get-ManagedControlPanelProcessId
        if ($controlProcessId) {
            return $controlProcessId
        }
        Start-Sleep -Milliseconds 250
    }
    throw "The installed control center did not register its process."
}

function Wait-PathAbsent {
    param([string]$Path, [int]$TimeoutSeconds = 180)
    $attempts = $TimeoutSeconds * 2
    for ($attempt = 0; $attempt -lt $attempts; $attempt++) {
        if (-not (Test-Path -LiteralPath $Path)) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out waiting for asynchronous cleanup of $Path"
}

function Assert-Autostart {
    param([bool]$Enabled)
    $value = $null
    if (Test-Path -LiteralPath $AutostartRunKey) {
        try {
            $value = Get-ItemPropertyValue -LiteralPath $AutostartRunKey -Name $ProductName -ErrorAction Stop
        } catch {
            $value = $null
        }
    }
    if ($Enabled) {
        Assert-True ($null -ne $value) "The first GUI install did not enable autostart."
        Assert-True ($value.IndexOf($ControlPanelExecutable, [StringComparison]::OrdinalIgnoreCase) -ge 0) "Autostart points to an unexpected executable."
        Assert-True ($value.IndexOf("--autostart", [StringComparison]::OrdinalIgnoreCase) -ge 0) "Autostart is missing the fixed --autostart argument."
    } else {
        Assert-True ($null -eq $value) "Upgrade unexpectedly re-enabled autostart."
    }
}

function Invoke-RelocationProcess {
    param([string]$Script, [string]$Source, [string]$Destination, [string]$Wheel)
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $Script),
        "-ParentProcessId", "2147483647",
        "-SourceRoot", ('"{0}"' -f $Source),
        "-DestinationRoot", ('"{0}"' -f $Destination),
        "-CurrentVersion", $PackageVersion,
        "-PackageSource", ('"{0}"' -f $Wheel),
        "-SkipUvBootstrap"
    ) -join " "
    $process = Start-Process `
        -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -ArgumentList $arguments `
        -WindowStyle Hidden `
        -PassThru
    Assert-True ($process.WaitForExit(600000)) "Installation relocation did not finish within ten minutes."
    return $process.ExitCode
}

Assert-True (Test-Path -LiteralPath $gui -PathType Leaf) "GUI binary is missing."
Assert-True (Test-Path -LiteralPath $package -PathType Leaf) "Server wheel is missing."

$initialInstallArguments = @{
    PackageSource = $package
    GuiSource = $gui
    SkipUvBootstrap = $true
    NonInteractive = $true
}
if ($env:PDF2ZH_WINDOWS_LIFECYCLE_PREPARED_CACHE -eq "1") {
    $initialInstallArguments.AllowPreparedDestination = $true
}
& (Join-Path $windowsDir "install.ps1") @initialInstallArguments
Assert-True (Test-Path -LiteralPath $ControlPanelExecutable) "Installer did not copy the GUI."
Assert-True ((Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim() -eq $PackageVersion) "Installed version marker is incorrect."
$savedExecutable = (Get-Content -Raw -LiteralPath $ExecutableFile).Trim()
Assert-True ($savedExecutable.StartsWith((Join-Path $AppRoot "runtime\tool-bin"), [StringComparison]::OrdinalIgnoreCase)) "The server executable is outside the product root."
Assert-True (Test-Path -LiteralPath (Join-Path $AppRoot "runtime\tools") -PathType Container) "The uv tool environment is outside the product root."
Assert-True (Test-Path -LiteralPath (Join-Path $AppRoot "runtime\python") -PathType Container) "The managed Python runtime is outside the product root."
Assert-True (Test-Path -LiteralPath (Join-Path $AppRoot "cache") -PathType Container) "The uv cache is outside the product root."
$shortcuts = @(Get-ChildItem -LiteralPath $StartMenuDir -Filter "*.lnk")
Assert-True ($shortcuts.Count -eq 2) "Installer must create exactly two Start menu shortcuts."
Assert-True ($shortcuts.Name -contains "$ProductName.lnk") "Start menu is missing the control center shortcut."
Assert-True ($shortcuts.Name -contains "卸载.lnk") "Start menu is missing the uninstall shortcut."
Assert-True (-not (Get-ListeningProcessId)) "Recovery installer started the server unexpectedly."

Start-Process -FilePath $ControlPanelExecutable -ArgumentList @("--post-install", "--enable-autostart") -WindowStyle Hidden
$firstControlProcessId = Wait-ControlPanel
Wait-ExpectedHealth
Assert-Autostart -Enabled $true

if ($env:PDF2ZH_WINDOWS_LIFECYCLE_TEST -ne "1") {
    $duplicate = Start-Process -FilePath $ControlPanelExecutable -PassThru -WindowStyle Hidden
    Assert-True ($duplicate.WaitForExit(10000)) "A duplicate control center instance remained running."
    Assert-True ((Get-ManagedControlPanelProcessId) -eq $firstControlProcessId) "Duplicate launch replaced the primary instance."
}

& (Join-Path $BinDir "stop-server.ps1") -Quiet
$listener = $null
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $ServerPort)
        $listener.Start()
        break
    } catch {
        $listener = $null
        if ($attempt -eq 39) {
            throw
        }
        Start-Sleep -Milliseconds 250
    }
}
try {
    $conflictDetected = $false
    try {
        & (Join-Path $BinDir "start-server.ps1") -Quiet
    } catch {
        $conflictDetected = $_.Exception.Message -like "*already used by another process*"
    }
    Assert-True $conflictDetected "Port conflict was not reported."
    Assert-True $listener.Server.IsBound "Port conflict handling stopped the unknown listener."
} finally {
    $listener.Stop()
}

$savedServerExecutable = (Get-Content -Raw -LiteralPath $ExecutableFile).Trim()
Set-Content -LiteralPath $ExecutableFile -Value (Join-Path $env:SystemRoot "System32\cmd.exe") -Encoding utf8
try {
    $startupFailed = $false
    try {
        & (Join-Path $BinDir "start-server.ps1") -Quiet
    } catch {
        $startupFailed = $true
    }
    Assert-True $startupFailed "A server startup failure was not surfaced."
} finally {
    Set-Content -LiteralPath $ExecutableFile -Value $savedServerExecutable -Encoding utf8
}

New-Item -ItemType Directory -Force -Path $DataDir, $LogsDir | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $DataDir "preserve-me") | Out-Null
New-Item -ItemType File -Force -Path $ControlLogFile | Out-Null
Assert-True (Test-Path -LiteralPath $DataDir) "Data directory cannot be opened because it is missing."
Assert-True (Test-Path -LiteralPath $ControlLogFile) "Control log cannot be opened because it is missing."

Remove-ItemProperty -LiteralPath $AutostartRunKey -Name $ProductName -Force -ErrorAction SilentlyContinue
Remove-ItemProperty -LiteralPath $AutostartApprovedKey -Name $ProductName -Force -ErrorAction SilentlyContinue
Set-Content -LiteralPath $InstalledVersionFile -Value "1.0.0" -Encoding ascii
& (Join-Path $windowsDir "install.ps1") `
    -PackageSource $package `
    -GuiSource $gui `
    -SkipUvBootstrap `
    -NonInteractive
Assert-True (-not (Get-Process -Id $firstControlProcessId -ErrorAction SilentlyContinue)) "Upgrade did not stop the path-validated old control center."
Assert-True (Test-Path -LiteralPath (Join-Path $DataDir "preserve-me")) "Upgrade removed persistent data."
Assert-Autostart -Enabled $false

Start-Process -FilePath $ControlPanelExecutable -ArgumentList "--post-install" -WindowStyle Hidden
$upgradedControlProcessId = Wait-ControlPanel
Wait-ExpectedHealth
$guiHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ControlPanelExecutable).Hash
$failedUpgrade = $false
try {
    & (Join-Path $windowsDir "install.ps1") -PackageSource (Join-Path $repoRoot "missing.whl") -GuiSource $gui -SkipUvBootstrap -NonInteractive
} catch {
    $failedUpgrade = $true
}
Assert-True $failedUpgrade "Invalid upgrade input did not fail."
Assert-True ((Get-FileHash -Algorithm SHA256 -LiteralPath $ControlPanelExecutable).Hash -eq $guiHash) "Failed upgrade changed the installed GUI."
Assert-True ($null -eq (Get-Process -Id $upgradedControlProcessId -ErrorAction SilentlyContinue)) "Failed upgrade did not stop the path-validated old GUI."
$recoveredControlProcessId = Wait-ControlPanel
Assert-True ($recoveredControlProcessId -ne $upgradedControlProcessId) "Failed upgrade did not restart the previous GUI."
Wait-ExpectedHealth

$selfUpdateRoot = Join-Path (Split-Path $AppRoot -Parent) (
    ".zotero-pdf2zh-pro-self-update-{0}" -f [guid]::NewGuid().ToString("N")
)
$selfUpdatePackage = Join-Path $selfUpdateRoot "package"
New-Item -ItemType Directory -Force -Path $selfUpdatePackage | Out-Null
Copy-Item -Path (Join-Path $windowsDir "*") -Destination $selfUpdatePackage -Recurse -Force
Copy-Item -LiteralPath $gui -Destination (Join-Path $selfUpdatePackage "$ProductName.exe") -Force
$applyUpdate = Join-Path $selfUpdatePackage "apply-update.ps1"
$applyArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ParentProcessId 2147483647 -PackageSource "{1}"' -f $applyUpdate, $package
$applyStdout = Join-Path $selfUpdateRoot "apply-update.stdout.log"
$applyStderr = Join-Path $selfUpdateRoot "apply-update.stderr.log"
$applyProcess = Start-Process `
    -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList $applyArguments `
    -WindowStyle Hidden `
    -RedirectStandardOutput $applyStdout `
    -RedirectStandardError $applyStderr `
    -PassThru
Assert-True ($applyProcess.WaitForExit(300000)) "Self-update bootstrap did not finish within five minutes."
$applyProcess.WaitForExit()
$applyProcess.Refresh()
$applyExitCode = $applyProcess.ExitCode
$applyOutput = @(
    Get-Content -LiteralPath $applyStdout -ErrorAction SilentlyContinue
    Get-Content -LiteralPath $applyStderr -ErrorAction SilentlyContinue
) | Where-Object {
    $_ -match '^\[install\]|^\[[1-5]/5\]|^Update failed|^error:|^downloading uv|^installing to|^everything|^WARNING:'
}
$applyOutput | ForEach-Object { Write-Host "[self-update-test] $_" }
if ($applyExitCode -ne 0) {
    Write-Host "[self-update-test] exit-code=$applyExitCode"
    Get-Content -LiteralPath $applyStdout -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "[self-update-test] stdout: $_" }
    Get-Content -LiteralPath $applyStderr -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "[self-update-test] stderr: $_" }
}
Assert-True (-not (Test-Path -LiteralPath (Join-Path $AppRoot "last-operation-error.txt") -PathType Leaf)) "Self-update reported an installation error."
if (-not (Test-Path -LiteralPath (Join-Path $AppRoot "runtime\uv\uv.exe") -PathType Leaf)) {
    foreach ($candidate in @(
        (Join-Path $windowsDir "install.ps1"),
        (Join-Path $selfUpdatePackage "install.ps1"),
        (Join-Path $BinDir "install.ps1")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash
            $hasPrivateUvCheck = $null -ne (Select-String -LiteralPath $candidate -SimpleMatch "Private uv is ready")
            Write-Host "[self-update-test] install-script=$candidate hash=$hash private-uv-check=$hasPrivateUvCheck"
        }
    }
    Get-ChildItem -LiteralPath (Join-Path $AppRoot "runtime\uv") -Force -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "[self-update-test] uv-directory-entry=$($_.FullName)" }
}
Assert-True (Test-Path -LiteralPath (Join-Path $AppRoot "runtime\uv\uv.exe") -PathType Leaf) "Self-update did not install the private uv executable."
Assert-True ($null -eq (Get-Process -Id $recoveredControlProcessId -ErrorAction SilentlyContinue)) "Self-update did not stop the previous GUI."
$selfUpdatedControlProcessId = Wait-ControlPanel
Assert-True ($selfUpdatedControlProcessId -ne $recoveredControlProcessId) "Self-update did not start a new GUI."
Wait-ExpectedHealth
Assert-True (Test-Path -LiteralPath (Join-Path $DataDir "preserve-me")) "Self-update removed persistent data."
Assert-Autostart -Enabled $false

Set-Content -LiteralPath $InstalledVersionFile -Value "99.0.0" -Encoding ascii
$downgradeBlocked = $false
try {
    & (Join-Path $windowsDir "install.ps1") -PackageSource $package -GuiSource $gui -SkipUvBootstrap -NonInteractive
} catch {
    $downgradeBlocked = $_.Exception.Message -like "*Downgrade blocked*"
}
Assert-True $downgradeBlocked "Downgrade was not blocked."
Set-Content -LiteralPath $InstalledVersionFile -Value $PackageVersion -Encoding ascii

$cacheBackup = Join-Path (Split-Path $AppRoot -Parent) ".lifecycle-cache-backup"
Remove-Item -LiteralPath $cacheBackup -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath $UvCacheDir -Destination $cacheBackup -Recurse -Force
& (Join-Path $BinDir "uninstall.ps1") -NonInteractive
Wait-PathAbsent -Path $BinDir
Wait-PathAbsent -Path $UvCacheDir
Assert-True (Test-Path -LiteralPath (Join-Path $DataDir "preserve-me")) "Default uninstall removed persistent data."
Assert-True (-not (Test-Path -LiteralPath $BinDir)) "Default uninstall left program files behind."
Assert-True (-not (Test-Path -LiteralPath $UvCacheDir)) "Default uninstall left the private cache behind."
Assert-True (-not (Test-Path -LiteralPath $StartMenuDir)) "Uninstall left Start menu shortcuts behind."
Assert-Autostart -Enabled $false

Copy-Item -LiteralPath $cacheBackup -Destination $UvCacheDir -Recurse -Force
Remove-Item -LiteralPath $cacheBackup -Recurse -Force
& (Join-Path $windowsDir "install.ps1") `
    -PackageSource $package `
    -GuiSource $gui `
    -SkipUvBootstrap `
    -AllowPreparedDestination `
    -NonInteractive
Assert-True (Test-Path -LiteralPath (Join-Path $DataDir "preserve-me")) "Reinstall did not preserve data."
& (Join-Path $BinDir "start-server.ps1") -Quiet
$recoveryDir = Join-Path $DataDir "relocation-task"
New-Item -ItemType Directory -Force -Path $recoveryDir | Out-Null
Set-Content -LiteralPath (Join-Path $recoveryDir "paragraph-recovery.json") -Value '{"version":1}' -Encoding utf8
$sourceRoot = $AppRoot
$rollbackRoot = Join-Path (Split-Path $sourceRoot -Parent) "rollback-target\zotero-pdf2zh-pro"
$relocateScript = Join-Path $BinDir "relocate.ps1"
$rollbackExit = Invoke-RelocationProcess `
    -Script $relocateScript `
    -Source $sourceRoot `
    -Destination $rollbackRoot `
    -Wheel (Join-Path $repoRoot "missing.whl")
Assert-True ($rollbackExit -ne 0) "Invalid relocation unexpectedly succeeded."
Assert-True (Test-Path -LiteralPath (Join-Path $recoveryDir "paragraph-recovery.json")) "Failed relocation removed the recovery checkpoint."
Assert-True (-not (Test-Path -LiteralPath $rollbackRoot)) "Failed relocation left the destination behind."
$rollbackControlProcessId = Wait-ControlPanel
Wait-ExpectedHealth
Stop-ManagedControlPanel

$destinationRoot = Join-Path (Split-Path $sourceRoot -Parent) "relocated\zotero-pdf2zh-pro"
$relocationExit = Invoke-RelocationProcess `
    -Script $relocateScript `
    -Source $sourceRoot `
    -Destination $destinationRoot `
    -Wheel $package
Assert-True ($relocationExit -eq 0) "Installation relocation failed."
$env:PDF2ZH_WINDOWS_APP_ROOT = $destinationRoot
. (Join-Path (Join-Path $destinationRoot "bin") "common.ps1")
Use-PrivateUvEnvironment
$relocatedControlProcessId = Wait-ControlPanel
Assert-True ($relocatedControlProcessId -ne $rollbackControlProcessId) "Relocation did not launch the new control center."
Wait-ExpectedHealth
Assert-True (Test-Path -LiteralPath (Join-Path $DataDir "relocation-task\paragraph-recovery.json")) "Relocation lost the recovery checkpoint."
Assert-True ((Get-SavedInstallRoot) -eq $destinationRoot) "Relocation did not commit the destination root."
Wait-PathAbsent -Path $sourceRoot
Assert-True (-not (Test-Path -LiteralPath $sourceRoot)) "Relocation left the old product root behind."

& (Join-Path $BinDir "uninstall.ps1") -PurgeData -NonInteractive
Wait-PathAbsent -Path $AppRoot
Assert-True (-not (Test-Path -LiteralPath $AppRoot)) "Purge uninstall left application data behind."
Assert-True (-not (Get-SavedInstallRoot)) "Purge uninstall left the saved installation root behind."

Write-Host "Windows GUI lifecycle checks passed."

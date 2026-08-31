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

Assert-True (Test-Path -LiteralPath $gui -PathType Leaf) "GUI binary is missing."
Assert-True (Test-Path -LiteralPath $package -PathType Leaf) "Server wheel is missing."

& (Join-Path $windowsDir "install.ps1") -PackageSource $package -GuiSource $gui -SkipUvBootstrap -NonInteractive
Assert-True (Test-Path -LiteralPath $ControlPanelExecutable) "Installer did not copy the GUI."
Assert-True ((Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim() -eq $PackageVersion) "Installed version marker is incorrect."
$shortcuts = @(Get-ChildItem -LiteralPath $StartMenuDir -Filter "*.lnk")
Assert-True ($shortcuts.Count -eq 2) "Installer must create exactly two Start menu shortcuts."
Assert-True ($shortcuts.Name -contains "$ProductName.lnk") "Start menu is missing the control center shortcut."
Assert-True ($shortcuts.Name -contains "卸载.lnk") "Start menu is missing the uninstall shortcut."
Assert-True (-not (Get-ListeningProcessId)) "Recovery installer started the server unexpectedly."

Start-Process -FilePath $ControlPanelExecutable -ArgumentList @("--post-install", "--enable-autostart") -WindowStyle Hidden
$firstControlProcessId = Wait-ControlPanel
Wait-ExpectedHealth
Assert-Autostart -Enabled $true

$duplicate = Start-Process -FilePath $ControlPanelExecutable -PassThru -WindowStyle Hidden
Assert-True ($duplicate.WaitForExit(10000)) "A duplicate control center instance remained running."
Assert-True ((Get-ManagedControlPanelProcessId) -eq $firstControlProcessId) "Duplicate launch replaced the primary instance."

& (Join-Path $BinDir "stop-server.ps1") -Quiet
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $ServerPort)
$listener.Start()
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
& (Join-Path $windowsDir "install.ps1") -PackageSource $package -GuiSource $gui -SkipUvBootstrap -NonInteractive
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

Set-Content -LiteralPath $InstalledVersionFile -Value "99.0.0" -Encoding ascii
$downgradeBlocked = $false
try {
    & (Join-Path $windowsDir "install.ps1") -PackageSource $package -GuiSource $gui -SkipUvBootstrap -NonInteractive
} catch {
    $downgradeBlocked = $_.Exception.Message -like "*Downgrade blocked*"
}
Assert-True $downgradeBlocked "Downgrade was not blocked."
Set-Content -LiteralPath $InstalledVersionFile -Value $PackageVersion -Encoding ascii

& (Join-Path $BinDir "uninstall.ps1") -NonInteractive
Start-Sleep -Seconds 5
Assert-True (Test-Path -LiteralPath (Join-Path $DataDir "preserve-me")) "Default uninstall removed persistent data."
Assert-True (-not (Test-Path -LiteralPath $BinDir)) "Default uninstall left program files behind."
Assert-True (-not (Test-Path -LiteralPath $StartMenuDir)) "Uninstall left Start menu shortcuts behind."
Assert-Autostart -Enabled $false

& (Join-Path $windowsDir "install.ps1") -PackageSource $package -GuiSource $gui -SkipUvBootstrap -NonInteractive
Assert-True (Test-Path -LiteralPath (Join-Path $DataDir "preserve-me")) "Reinstall did not preserve data."
& (Join-Path $BinDir "start-server.ps1") -Quiet
& (Join-Path $BinDir "stop-server.ps1") -Quiet
& (Join-Path $BinDir "uninstall.ps1") -PurgeData -NonInteractive
Start-Sleep -Seconds 5
Assert-True (-not (Test-Path -LiteralPath $AppRoot)) "Purge uninstall left application data behind."

Write-Host "Windows GUI lifecycle checks passed."

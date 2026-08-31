param(
    [string]$PackageSource,
    [string]$GuiSource,
    [switch]$SkipUvBootstrap,
    [switch]$NoShortcuts,
    [switch]$NonInteractive
)

. (Join-Path $PSScriptRoot "common.ps1")
Assert-WindowsX64

$managementFiles = @(
    "common.ps1",
    "install.ps1",
    "start-server.ps1",
    "stop-server.ps1",
    "view-log.ps1",
    "uninstall.ps1",
    "install.cmd",
    "start-server.cmd",
    "stop-server.cmd",
    "view-log.cmd",
    "uninstall.cmd"
)

function Install-Uv {
    Write-Host "Installing uv from the official Astral installer..."
    $installer = Invoke-RestMethod "https://astral.sh/uv/install.ps1"
    Invoke-Expression $installer
}

function Copy-LegacyData {
    param([string]$UvExecutable)
    if (Test-Path -LiteralPath $DataDir) {
        $existing = Get-ChildItem -Force -LiteralPath $DataDir -ErrorAction SilentlyContinue
        if ($existing) {
            return
        }
    }
    $toolRoot = (& $UvExecutable tool dir 2>$null | Select-Object -Last 1).Trim()
    if (-not $toolRoot) {
        return
    }
    $legacyData = Join-Path (Join-Path $toolRoot $ProductName) "Lib\site-packages\translates"
    if (-not (Test-Path -LiteralPath $legacyData)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    Copy-Item -Path (Join-Path $legacyData "*") -Destination $DataDir -Recurse -Force
    Write-Host "Copied legacy task data to $DataDir"
}

function Resolve-GuiSource {
    $candidate = if ($GuiSource) {
        [IO.Path]::GetFullPath($GuiSource)
    } else {
        Join-Path $PSScriptRoot "$ProductName.exe"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "The Windows control center executable was not found at $candidate"
    }
    $stream = [IO.File]::OpenRead($candidate)
    try {
        if ($stream.Length -lt 2 -or $stream.ReadByte() -ne 0x4d -or $stream.ReadByte() -ne 0x5a) {
            throw "The control center executable is not a valid Windows PE file."
        }
    } finally {
        $stream.Dispose()
    }
    return [IO.Path]::GetFullPath($candidate)
}

function Copy-PackageFiles {
    param([string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($file in $managementFiles) {
        $source = Join-Path $PSScriptRoot $file
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "The Windows package is missing $file"
        }
        Copy-Item -LiteralPath $source -Destination (Join-Path $Destination $file) -Force
    }
}

function Backup-InstalledFiles {
    param([string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($file in @($managementFiles) + @("$ProductName.exe")) {
        $source = Join-Path $BinDir $file
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $Destination $file) -Force
        }
    }
}

function Restore-InstalledFiles {
    param([string]$BackupDirectory)
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    foreach ($file in @($managementFiles) + @("$ProductName.exe")) {
        Remove-Item -LiteralPath (Join-Path $BinDir $file) -Force -ErrorAction SilentlyContinue
        $backup = Join-Path $BackupDirectory $file
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            Copy-Item -LiteralPath $backup -Destination (Join-Path $BinDir $file) -Force
        }
    }
}

function Install-StagedFiles {
    param([string]$StagedBin)
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $stagedGui = Join-Path $StagedBin "$ProductName.exe"
    $atomicBackup = Join-Path (Split-Path $StagedBin -Parent) "control-panel.previous.exe"
    if (Test-Path -LiteralPath $ControlPanelExecutable -PathType Leaf) {
        [IO.File]::Replace($stagedGui, $ControlPanelExecutable, $atomicBackup, $true)
    } else {
        [IO.File]::Move($stagedGui, $ControlPanelExecutable)
    }
    foreach ($file in $managementFiles) {
        Copy-Item -LiteralPath (Join-Path $StagedBin $file) -Destination (Join-Path $BinDir $file) -Force
    }
}

function Install-Shortcuts {
    if ($NoShortcuts) {
        return
    }
    New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
    Get-ChildItem -LiteralPath $StartMenuDir -Filter "*.lnk" -ErrorAction SilentlyContinue |
        Remove-Item -Force
    $shell = New-Object -ComObject WScript.Shell

    $controlShortcut = $shell.CreateShortcut((Join-Path $StartMenuDir "$ProductName.lnk"))
    $controlShortcut.TargetPath = $ControlPanelExecutable
    $controlShortcut.WorkingDirectory = $AppRoot
    $controlShortcut.Save()

    $uninstallShortcut = $shell.CreateShortcut((Join-Path $StartMenuDir "卸载.lnk"))
    $uninstallShortcut.TargetPath = Join-Path $BinDir "uninstall.cmd"
    $uninstallShortcut.WorkingDirectory = $AppRoot
    $uninstallShortcut.Save()
}

$resolvedGuiSource = Resolve-GuiSource
$firstGuiInstall = -not (Test-Path -LiteralPath $ControlPanelExecutable -PathType Leaf)
$previousVersion = if (Test-Path -LiteralPath $InstalledVersionFile) {
    (Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim()
} else {
    $null
}
if ($previousVersion) {
    try {
        if ([version]$PackageVersion -lt [version]$previousVersion) {
            throw "Downgrade blocked: installed version is $previousVersion, candidate is $PackageVersion."
        }
    } catch [System.Management.Automation.RuntimeException] {
        throw
    } catch {
        throw "Cannot compare installed version '$previousVersion' with '$PackageVersion'."
    }
}

New-Item -ItemType Directory -Force -Path $AppRoot, $DataDir, $LogsDir | Out-Null
$stagingRoot = Join-Path $AppRoot (".install-{0}" -f [guid]::NewGuid().ToString("N"))
$stagedBin = Join-Path $stagingRoot "new"
$backupBin = Join-Path $stagingRoot "previous"
$serverInstalled = $false
$filesInstalled = $false
$previousControlPanelRunning = $null -ne (Get-ManagedControlPanelProcessId)

try {
    Copy-PackageFiles -Destination $stagedBin
    Copy-Item -LiteralPath $resolvedGuiSource -Destination (Join-Path $stagedBin "$ProductName.exe") -Force
    Backup-InstalledFiles -Destination $backupBin
    Stop-ManagedControlPanel

    $existingStop = Join-Path $BinDir "stop-server.ps1"
    if (Test-Path -LiteralPath $existingStop) {
        & $existingStop -Quiet
    } else {
        & (Join-Path $PSScriptRoot "stop-server.ps1") -Quiet
    }

    $uv = Get-UvExecutable
    if (-not $uv) {
        if ($SkipUvBootstrap) {
            throw "uv is required but was not found."
        }
        Install-Uv
        $uv = Get-UvExecutable
    }
    if (-not $uv) {
        throw "uv installation completed but uv.exe could not be found."
    }

    Copy-LegacyData -UvExecutable $uv
    $package = if ($PackageSource) {
        [IO.Path]::GetFullPath($PackageSource)
    } else {
        "$ProductName==$PackageVersion"
    }
    Write-Host "Installing $package with managed Python 3.13..."
    & $uv tool install --python 3.13 --managed-python --force --no-config --default-index https://pypi.org/simple $package
    if ($LASTEXITCODE -ne 0) {
        throw "uv tool install failed with exit code $LASTEXITCODE."
    }
    $serverInstalled = $true

    $toolBin = (& $uv tool dir --bin | Select-Object -Last 1).Trim()
    $serverExecutable = Join-Path $toolBin "$ProductName.exe"
    if (-not (Test-Path -LiteralPath $serverExecutable)) {
        throw "Installed server executable was not found at $serverExecutable"
    }
    & $serverExecutable --help | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The installed server failed its startup smoke check."
    }

    $filesInstalled = $true
    Install-StagedFiles -StagedBin $stagedBin
    Save-ManagedProcess -ProcessId 0 -ServerExecutable $serverExecutable
    Remove-ManagedProcessState
    $versionTemp = Join-Path $stagingRoot "installed-version.txt"
    Set-Content -LiteralPath $versionTemp -Value $PackageVersion -Encoding ascii
    Move-Item -LiteralPath $versionTemp -Destination $InstalledVersionFile -Force
    Install-Shortcuts
} catch {
    $installError = $_
    if ($filesInstalled) {
        try {
            Restore-InstalledFiles -BackupDirectory $backupBin
            if ($previousVersion) {
                Set-Content -LiteralPath $InstalledVersionFile -Value $previousVersion -Encoding ascii
            } else {
                Remove-Item -LiteralPath $InstalledVersionFile -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Warning "Failed to restore the previous control center files: $_"
        }
    }
    if ($serverInstalled -and $previousVersion -and $uv) {
        Write-Warning "Installation failed; restoring server $previousVersion."
        & $uv tool install --python 3.13 --managed-python --force --no-config --default-index https://pypi.org/simple "$ProductName==$previousVersion"
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "The previous server version could not be restored automatically."
        }
    }
    if ($previousControlPanelRunning -and (Test-Path -LiteralPath $ControlPanelExecutable)) {
        try {
            Start-Process -FilePath $ControlPanelExecutable -ArgumentList "--post-install"
        } catch {
            Write-Warning "The previous control center could not be restarted automatically."
        }
    }
    throw $installError
} finally {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Installation complete."
Write-Host "Data: $DataDir"
Write-Host "Logs: $LogsDir"

if (-not $NonInteractive) {
    $arguments = @("--post-install")
    if ($firstGuiInstall) {
        $arguments += "--enable-autostart"
    }
    Start-Process -FilePath $ControlPanelExecutable -ArgumentList $arguments
}

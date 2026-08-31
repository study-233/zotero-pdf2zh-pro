param(
    [string]$PackageSource,
    [switch]$SkipUvBootstrap,
    [switch]$NoShortcuts,
    [switch]$NonInteractive
)

. (Join-Path $PSScriptRoot "common.ps1")
Assert-WindowsX64

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

function Install-ManagementFiles {
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $files = @(
        "common.ps1",
        "install.ps1",
        "start-server.ps1",
        "stop-server.ps1",
        "view-log.ps1",
        "uninstall.ps1",
        "安装.cmd",
        "启动服务.cmd",
        "停止服务.cmd",
        "查看日志.cmd",
        "卸载.cmd"
    )
    foreach ($file in $files) {
        $source = Join-Path $PSScriptRoot $file
        $destination = Join-Path $BinDir $file
        if (-not (Test-PathEqual -Left $source -Right $destination)) {
            Copy-Item -LiteralPath $source -Destination $destination -Force
        }
    }
}

function Install-Shortcuts {
    if ($NoShortcuts) {
        return
    }
    New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcuts = @{
        "启动服务.lnk" = "启动服务.cmd"
        "停止服务.lnk" = "停止服务.cmd"
        "查看日志.lnk" = "查看日志.cmd"
        "卸载.lnk" = "卸载.cmd"
    }
    foreach ($shortcutName in $shortcuts.Keys) {
        $shortcut = $shell.CreateShortcut((Join-Path $StartMenuDir $shortcutName))
        $shortcut.TargetPath = Join-Path $BinDir $shortcuts[$shortcutName]
        $shortcut.WorkingDirectory = $AppRoot
        $shortcut.Save()
    }
}

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

$toolBin = (& $uv tool dir --bin | Select-Object -Last 1).Trim()
$serverExecutable = Join-Path $toolBin "$ProductName.exe"
if (-not (Test-Path -LiteralPath $serverExecutable)) {
    throw "Installed server executable was not found at $serverExecutable"
}
& $serverExecutable --help | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The installed server failed its startup smoke check."
}

New-Item -ItemType Directory -Force -Path $AppRoot, $DataDir, $LogsDir | Out-Null
Install-ManagementFiles
Save-ManagedProcess -ProcessId 0 -ServerExecutable $serverExecutable
Remove-ManagedProcessState
Install-Shortcuts

Write-Host ""
Write-Host "Installation complete. The server was not started automatically."
Write-Host "Use the Start menu shortcut or 启动服务.cmd when you want to run it."
Write-Host "Data: $DataDir"
Write-Host "Logs: $LogFile"

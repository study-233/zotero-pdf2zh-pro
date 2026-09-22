param([Parameter(Mandatory = $true)][string]$Package)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows\prepare-webview2.ps1') -FunctionsOnly
if (-not (Test-WebView2Runtime)) { throw 'The GUI smoke-test runner must have WebView2 Runtime; test missing-runtime setup separately in a clean VM.' }
$root = Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-package-smoke-' + [guid]::NewGuid().ToString('N'))
$process = $null
$processPath = $null
$savedEnvironment = @{}
foreach ($name in @('PATH', 'LOCALAPPDATA', 'PDF2ZH_WINDOWS_APP_ROOT', 'PDF2ZH_WINDOWS_REGISTRY_KEY', 'PDF2ZH_WINDOWS_LIFECYCLE_TEST', 'PDF2ZH_UPDATE_CONTRACT_PACKAGE')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Wait-ControlWindow {
    param([Diagnostics.Process]$Process)
    $shown = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        $Process.Refresh()
        if ($Process.HasExited) { throw "Packaged EXE exited before opening a window: $($Process.ExitCode)" }
        if ($Process.MainWindowHandle -ne [IntPtr]::Zero -and $Process.MainWindowTitle -eq 'zotero-pdf2zh-pro') {
            $shown = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $shown) { throw 'The actual packaged control-center window did not open within 60 seconds.' }
}

function Stop-TestControl {
    param([Diagnostics.Process]$Process, [string]$ExpectedPath)
    if (-not $Process) { return }
    $Process.Refresh()
    if ($Process.HasExited) { return }
    if (-not [IO.Path]::GetFullPath($Process.Path).Equals([IO.Path]::GetFullPath($ExpectedPath), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to stop a process outside the test installation: $($Process.Id)"
    }
    [void]$Process.CloseMainWindow()
    if (-not $Process.WaitForExit(5000)) { $Process.Kill(); $Process.WaitForExit() }
}

try {
    $unpacked = Join-Path $root 'package with spaces'
    $working = Join-Path $root 'independent-working-directory'
    $env:LOCALAPPDATA = Join-Path $root 'local-app-data'
    New-Item -ItemType Directory -Path $working, $env:LOCALAPPDATA -Force | Out-Null
    Expand-Archive -LiteralPath (Resolve-Path $Package).Path -DestinationPath $unpacked
    $env:PDF2ZH_UPDATE_CONTRACT_PACKAGE = (Resolve-Path $Package).Path
    $manifest = Join-Path $PSScriptRoot '..\windows-app\src-tauri\Cargo.toml'
    & cargo +stable-x86_64-pc-windows-msvc test --release --locked --target x86_64-pc-windows-msvc --manifest-path $manifest update_handoff_binds_packaged_script
    if ($LASTEXITCODE -ne 0) { throw 'Packaged updater rejected production or legacy invocation arguments.' }
    Write-Host 'Final ZIP accepts production and legacy updater commands, including Chinese characters and spaces.'
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $env:PDF2ZH_WINDOWS_APP_ROOT = Join-Path $root 'fresh-app'
    $env:PDF2ZH_WINDOWS_REGISTRY_KEY = 'HKCU:\Software\pdf2zh-smoke-' + [guid]::NewGuid().ToString('N')
    $env:PDF2ZH_WINDOWS_LIFECYCLE_TEST = '1'
    $processPath = Join-Path $unpacked 'zotero-pdf2zh-pro.exe'
    $process = Start-Process -FilePath $processPath -WorkingDirectory $working -PassThru -WindowStyle Hidden
    Wait-ControlWindow -Process $process
    Write-Host 'Final ZIP opens its real control-center window with an isolated working directory and PATH.'
    Stop-TestControl -Process $process -ExpectedPath $processPath
    $process = $null

    $installedRoot = Join-Path $root 'installed 中文 with spaces'
    $installedBin = Join-Path $installedRoot 'bin'
    New-Item -ItemType Directory -Path $installedBin -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $unpacked 'zotero-pdf2zh-pro.exe'), (Join-Path $unpacked 'common.ps1') -Destination $installedBin
    $packagedCommon = Get-Content -Raw -LiteralPath (Join-Path $unpacked 'common.ps1')
    $versionMatch = [regex]::Match($packagedCommon, '(?m)^\$PackageVersion\s*=\s*"([^"]+)"')
    if (-not $versionMatch.Success) { throw 'Cannot read the installation version from packaged common.ps1.' }
    Set-Content -LiteralPath (Join-Path $installedRoot 'installed-version.txt') -Value $versionMatch.Groups[1].Value -Encoding ascii
    $env:PDF2ZH_WINDOWS_APP_ROOT = $null
    $processPath = Join-Path $installedBin 'zotero-pdf2zh-pro.exe'
    $process = Start-Process -FilePath $processPath -WorkingDirectory $working -PassThru -WindowStyle Hidden
    Wait-ControlWindow -Process $process
    $controlLog = Get-Content -Raw -LiteralPath (Join-Path $installedRoot 'logs\control-panel.log') -Encoding utf8
    if (-not $controlLog.Contains("source=executable-installation; root=$installedRoot;")) {
        throw 'The installed EXE did not discover its own installation without a registry entry or environment override.'
    }
    if (-not $controlLog.Contains("current=$processPath; installed=$processPath; recognized=true")) {
        throw 'The installed EXE did not recognize the expected installed control-center path.'
    }
    $registeredProcessId = (Get-Content -Raw -LiteralPath (Join-Path $installedRoot 'control-panel.pid')).Trim()
    $registeredExecutable = (Get-Content -Raw -LiteralPath (Join-Path $installedRoot 'control-panel-executable.txt') -Encoding utf8).Trim()
    if ($registeredProcessId -ne [string]$process.Id -or -not $registeredExecutable.Equals($processPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The installed EXE registered an unexpected control-center process or executable.'
    }
    Write-Host 'Installed EXE discovers its own root with no saved registration or path override, including Chinese characters and spaces.'
} finally {
    try {
        Stop-TestControl -Process $process -ExpectedPath $processPath
    } finally {
        foreach ($name in $savedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
        }
    }
    $resolvedRoot = [IO.Path]::GetFullPath($root)
    $expectedParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ((Split-Path $resolvedRoot -Parent).TrimEnd('\') -ne $expectedParent -or
        (Split-Path $resolvedRoot -Leaf) -notlike 'pdf2zh-package-smoke-*') {
        throw "Refusing to remove an unexpected test directory: $resolvedRoot"
    }
    Remove-Item -LiteralPath $resolvedRoot -Recurse -Force -ErrorAction SilentlyContinue
}

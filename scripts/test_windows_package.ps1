param([Parameter(Mandatory = $true)][string]$Package)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows\prepare-webview2.ps1') -FunctionsOnly
if (-not (Test-WebView2Runtime)) { throw 'The GUI smoke-test runner must have WebView2 Runtime; test missing-runtime setup separately in a clean VM.' }
$root = Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-package-smoke-' + [guid]::NewGuid().ToString('N'))
$process = $null
$savedEnvironment = @{}
foreach ($name in @('PATH', 'PDF2ZH_WINDOWS_APP_ROOT', 'PDF2ZH_WINDOWS_REGISTRY_KEY', 'PDF2ZH_WINDOWS_LIFECYCLE_TEST')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $unpacked = Join-Path $root 'package with spaces'
    $working = Join-Path $root 'independent-working-directory'
    New-Item -ItemType Directory -Path $working -Force | Out-Null
    Expand-Archive -LiteralPath (Resolve-Path $Package).Path -DestinationPath $unpacked
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $env:PDF2ZH_WINDOWS_APP_ROOT = Join-Path $root 'fresh-app'
    $env:PDF2ZH_WINDOWS_REGISTRY_KEY = 'HKCU:\Software\pdf2zh-smoke-' + [guid]::NewGuid().ToString('N')
    $env:PDF2ZH_WINDOWS_LIFECYCLE_TEST = $null
    $process = Start-Process -FilePath (Join-Path $unpacked 'zotero-pdf2zh-pro.exe') -WorkingDirectory $working -PassThru
    $shown = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        $process.Refresh()
        if ($process.HasExited) { throw "Packaged EXE exited before opening a window: $($process.ExitCode)" }
        if ($process.MainWindowHandle -ne [IntPtr]::Zero -and $process.MainWindowTitle -eq 'zotero-pdf2zh-pro') {
            $shown = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $shown) { throw 'The actual packaged control-center window did not open within 60 seconds.' }
    Write-Host 'Final ZIP opens its real control-center window with an isolated working directory and PATH.'
} finally {
    if ($process -and -not $process.HasExited) {
        [void]$process.CloseMainWindow()
        if (-not $process.WaitForExit(5000)) { $process.Kill(); $process.WaitForExit() }
    }
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-control-test-' + [guid]::NewGuid().ToString('N'))
$previousRoot = $env:PDF2ZH_WINDOWS_APP_ROOT
$processes = @()
try {
    $env:PDF2ZH_WINDOWS_APP_ROOT = Join-Path $testRoot 'installation'
    . (Join-Path $PSScriptRoot 'windows/common.ps1')
    $unrelated = Join-Path $testRoot 'other-installation/zotero-pdf2zh-pro.exe'
    New-Item -ItemType Directory -Force -Path $BinDir, (Split-Path $unrelated -Parent) | Out-Null
    # Real, harmless long-lived processes with the same image name as the GUI.
    foreach ($path in @($ControlPanelExecutable, $unrelated)) {
        Copy-Item -LiteralPath "$env:SystemRoot/System32/ping.exe" -Destination $path
    }
    foreach ($path in @($ControlPanelExecutable, $ControlPanelExecutable, $unrelated)) {
        $processes += Start-Process -FilePath $path -ArgumentList '-n', '60', '127.0.0.1' -WindowStyle Hidden -PassThru
    }
    # A stale marker pointing at another installation must not authorize a stop.
    Set-Content -LiteralPath $ControlPanelPidFile -Value $processes[2].Id
    Set-Content -LiteralPath $ControlPanelExecutableFile -Value $ControlPanelExecutable
    Stop-ManagedControlPanel
    foreach ($process in $processes[0..1]) {
        if (-not $process.WaitForExit(1000)) { throw 'An unrecorded duplicate controller survived.' }
    }
    $processes[2].Refresh()
    if ($processes[2].HasExited) { throw 'An unrelated installation was stopped.' }
    if ((Test-Path -LiteralPath $ControlPanelPidFile) -or (Test-Path -LiteralPath $ControlPanelExecutableFile)) {
        throw 'Stale controller state survived cleanup.'
    }
    Stop-ManagedControlPanel
    Write-Host 'Duplicate legacy controllers stop without affecting another installation.'
} finally {
    foreach ($process in $processes) {
        $process.Refresh()
        if (-not $process.HasExited) { Stop-Process -InputObject $process; $process.WaitForExit() }
        $process.Dispose()
    }
    $env:PDF2ZH_WINDOWS_APP_ROOT = $previousRoot
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if ((Split-Path $resolved -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or
        (Split-Path $resolved -Leaf) -notlike 'pdf2zh-control-test-*') { throw 'Unsafe test cleanup path.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
}

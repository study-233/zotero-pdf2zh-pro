# Check the on-disk plugin protocol with the actual scheduler and an isolated pending update.
param([Parameter(Mandatory)][string]$Package, [string]$Receipt)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'workspace.ps1')
. (Join-Path $PSScriptRoot 'test_windows_process_helpers.ps1')
$run = New-WorkspaceRun -Name 'win-plugin-queue'
$root = Join-Path $run.work 'installation'
$bin = Join-Path $root 'bin'
$updates = Join-Path $root 'updates'
$registry = 'HKCU:\Software\pdf2zh-stable-upgrade-' + [guid]::NewGuid().ToString('N')
$env:PDF2ZH_WINDOWS_APP_ROOT = $root
$env:PDF2ZH_WINDOWS_REGISTRY_KEY = $registry
$env:PDF2ZH_WINDOWS_PORT = '18895'
$env:PDF2ZH_WINDOWS_START_MENU_DIR = Join-Path $run.work 'menu'
Remove-Item Env:PDF2ZH_WINDOWS_LIFECYCLE_TEST -ErrorAction SilentlyContinue
$result = @{status='failed';stage='native request recovery'}
$failure = $null
try {
    Expand-Archive -LiteralPath $Package -DestinationPath $bin
    [void][IO.Directory]::CreateDirectory($updates)
    $exe = Join-Path $bin 'zotero-pdf2zh-pro.exe'
    $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($exe).FileVersion
    $target = "$(([version]$version).Major + 1).0.0"
    [IO.File]::WriteAllText((Join-Path $root 'installed-version.txt'),$version)
    [IO.File]::WriteAllText((Join-Path $root 'settings.json'),'{"autoUpdateDisabled":true}')
    [IO.File]::WriteAllText((Join-Path $updates 'plugin-policy.json'),'{"follow":true}')
    $stateFile = Join-Path $updates 'state.json'
    $state = @{phase='ready';version=$target;cleanupAt=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()}
    [IO.File]::WriteAllText($stateFile,($state | ConvertTo-Json -Compress))
    # Resume a claimed request, then consume the queued cancel. A stopped service stays stopped.
    [IO.File]::WriteAllText((Join-Path $updates 'plugin-processing.json'),(@{action='update';target=$version} | ConvertTo-Json -Compress))
    [IO.File]::WriteAllText((Join-Path $updates 'plugin-request.json'),(@{action='cancel';target=$target} | ConvertTo-Json -Compress))
    Start-Process -FilePath $exe -ArgumentList '--post-install','--keep-stopped' -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddMinutes(4)
    do {
        Start-Sleep -Milliseconds 500
        $current = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
    } until ($current.phase -eq 'cancelled' -or [DateTime]::UtcNow -ge $deadline)
    if ($current.phase -ne 'cancelled' -or $current.version -ne $target) { throw 'The native scheduler did not cancel the pending update.' }
    if ((Test-Path -LiteralPath (Join-Path $updates 'plugin-request.json')) -or (Test-Path -LiteralPath (Join-Path $updates 'plugin-processing.json'))) { throw 'Queue was not consumed.' }
    $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    if ($listeners | Where-Object Port -eq ([int]$env:PDF2ZH_WINDOWS_PORT)) { throw 'A stopped service started unexpectedly.' }
    $result.status='passed'
    $result.version=$version
    $result.checks=@('recover claimed request','cancel matching pending target','keep service stopped')
} catch { $failure=$_; throw }
finally { Complete-IsolatedUpgrade -Run $run -Roots @($run.work) -Registry $registry -Result $result -Failure $failure -Receipt $Receipt }

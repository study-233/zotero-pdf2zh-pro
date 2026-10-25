param(
    [Parameter(Mandatory=$true)][string]$PreviousPackage,
    [Parameter(Mandatory=$true)][string]$PreviousWheel,
    [Parameter(Mandatory=$true)][string]$PreviousVersion,
    [Parameter(Mandatory=$true)][string]$Package,
    [Parameter(Mandatory=$true)][string]$Wheel,
    [Parameter(Mandatory=$true)][string]$Version,
    [string]$DownloadCache,
    [string]$Receipt, [string]$PlanSha256, [string]$HarnessSha256, [string]$SourceCommit
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ([version]$PreviousVersion -ge [version]$Version) {throw 'Upgrade requires genuinely different, increasing versions.'}
. (Join-Path $PSScriptRoot 'workspace.ps1')
. (Join-Path $PSScriptRoot 'test_windows_process_helpers.ps1')
$run=New-WorkspaceRun -Name 'stable-upgrade'
$root=Join-Path $run.work 'app 中文'
$registry='HKCU:\Software\pdf2zh-stable-upgrade-'+[guid]::NewGuid().ToString('N')
$saved=@{}
foreach ($name in @('PDF2ZH_WINDOWS_APP_ROOT','PDF2ZH_WINDOWS_PORT','PDF2ZH_WINDOWS_REGISTRY_KEY',
    'PDF2ZH_WINDOWS_START_MENU_DIR','PDF2ZH_WINDOWS_LIFECYCLE_TEST','UV_TOOL_DIR','UV_TOOL_BIN_DIR',
    'UV_INSTALL_DIR','UV_PYTHON_INSTALL_DIR','UV_CACHE_DIR','PDF2ZH_DATA_DIR')) {
    $saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')
}
$passed=$false
$testFailure=$null
$stage='baseline-setup'
try {
    $old=Join-Path $run.work 'old';$new=Join-Path $run.work 'new'
    Expand-Archive -LiteralPath (Resolve-Path $PreviousPackage).Path -DestinationPath $old
    Expand-Archive -LiteralPath (Resolve-Path $Package).Path -DestinationPath $new
    $env:PDF2ZH_WINDOWS_APP_ROOT=$root
    $env:PDF2ZH_WINDOWS_PORT='18892'
    $env:PDF2ZH_WINDOWS_REGISTRY_KEY=$registry
    $env:PDF2ZH_WINDOWS_START_MENU_DIR=Join-Path $run.work 'menu'
    $env:PDF2ZH_WINDOWS_LIFECYCLE_TEST='1'
    function Assert-True([bool]$Condition,[string]$Message) {if (-not $Condition) {throw $Message}}
    function Wait-Version([string]$Expected) {
        for ($i=0;$i -lt 120;$i++) {
            $health=Get-ServerHealth
            if ($health -and $health.version -eq $Expected -and $health.workspace.path -eq $DataDir) {return}
            Start-Sleep -Milliseconds 500
        }
        throw "Isolated service did not become healthy at $Expected"
    }
    function Get-TestPython {
        $python=Join-Path (Split-Path (Get-ServerExecutable)) 'python.exe'
        if (-not (Test-Path -LiteralPath $python)) {$python=Get-ToolPythonExecutable}
        Assert-True ($python -and (Test-Path -LiteralPath $python)) 'Installed interpreter is missing.'
        return $python
    }
    $installArguments=@()
    if ($DownloadCache) {
        $cache=(Resolve-Path -LiteralPath $DownloadCache).Path
        $allowed=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../.tmp')).TrimEnd('\')+'\'
        if (-not $cache.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)) {throw 'Only a repository test download cache may be reused.'}
        New-Item -ItemType Directory -Force -Path (Join-Path $root 'runtime')|Out-Null
        Copy-Item -LiteralPath (Join-Path $cache 'cache') -Destination (Join-Path $root 'cache') -Recurse
        foreach ($part in @('python','uv')) {
            Copy-Item -LiteralPath (Join-Path $cache "runtime/$part") -Destination (Join-Path $root "runtime/$part") -Recurse
        }
        $installArguments+='-AllowPreparedDestination'
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $old 'install.ps1') -PackageSource (Resolve-Path $PreviousWheel).Path -GuiSource (Join-Path $old 'zotero-pdf2zh-pro.exe') -NonInteractive @installArguments
    Assert-True ($LASTEXITCODE -eq 0) 'Baseline installation failed.'
    . (Join-Path $root 'bin/common.ps1')
    Assert-True ($PackageVersion -eq $PreviousVersion) 'Baseline package version does not match the frozen plan.'
    & (Get-TestPython) (Join-Path $PSScriptRoot 'release-data-fixtures.py') seed $DataDir
    Assert-True ($LASTEXITCODE -eq 0) 'Cannot prepare the baseline data format.'
    Set-Content -LiteralPath (Join-Path $root 'settings.json') -Encoding utf8 -Value '{"autoUpdateDisabled":true,"preservedUpgradeSetting":"keep"}'
    Start-Process -FilePath $ControlPanelExecutable -ArgumentList '--post-install' -WindowStyle Hidden | Out-Null
    Wait-Version $PreviousVersion
    $oldPid=Wait-TestControlStartup
    $stage='upgrade'
    # Use the final package's actual handoff script while the real previous GUI/service are installed.
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "{0}" -ParentProcessId {1} -InstallRoot "{2}" -PackageSource "{3}"' -f (Join-Path $new 'apply-update.ps1'),$oldPid,$root,(Resolve-Path $Wheel).Path
    $stdout=Join-Path $run.report 'upgrade.stdout.log';$stderr=Join-Path $run.report 'upgrade.stderr.log'
    $updater=Start-Process -FilePath powershell -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $null=$updater.Handle
    Stop-ManagedControlPanel
    Assert-True ($updater.WaitForExit(600000)) 'Upgrade handoff timed out.'
    $updater.WaitForExit()
    $updater.Refresh()
    Assert-True ($updater.ExitCode -eq 0) 'The candidate update script failed.'
    . (Join-Path $root 'bin/common.ps1')
    Assert-True ($PackageVersion -eq $Version) 'The installed control scripts did not change version.'
    Wait-Version $Version
    $newPid=Wait-TestControlStartup
    Assert-True ($newPid -ne $oldPid) 'The previous control center did not hand over.'
    & (Get-TestPython) (Join-Path $PSScriptRoot 'release-data-fixtures.py') verify $DataDir
    Assert-True ($LASTEXITCODE -eq 0) 'The candidate cannot read preserved data.'
    $settings=Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $root 'settings.json')|ConvertFrom-Json
    Assert-True ($settings.preservedUpgradeSetting -eq 'keep' -and $settings.autoUpdateDisabled) 'Upgrade changed existing settings.'
    $transaction=Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $root 'updates/transaction.json')|ConvertFrom-Json
    Assert-True ($transaction.phase -eq 'committed' -and $transaction.oldVersion -eq $PreviousVersion -and $transaction.newVersion -eq $Version) 'The cross-version transaction was not committed.'
    $passed=$true
} catch { $testFailure=$_; throw } finally {
    foreach ($name in $saved.Keys) {[Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')}
    $result=@{previousVersion=$PreviousVersion;version=$Version;stage=$stage;status=$(if($passed){'passed'}else{'failed'});
      packageSha256=(Get-TestFileSha256 $Package);wheelSha256=(Get-TestFileSha256 $Wheel)}
    $result.commit=$SourceCommit; $result.planSha256=$PlanSha256; $result.harnessSha256=$HarnessSha256
    $result.previousPackageSha256=Get-TestFileSha256 $PreviousPackage
    $result.previousWheelSha256=Get-TestFileSha256 $PreviousWheel
    Complete-IsolatedUpgrade -Run $run -Roots @($run.work) -Registry $registry -Result $result -Failure $testFailure -Receipt $Receipt
}

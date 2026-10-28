param(
    [Parameter(Mandatory=$true)][string]$Root,
    [Parameter(Mandatory=$true)][string]$Package,
    [Parameter(Mandatory=$true)][string]$Wheel,
    [string]$Receipt
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$Root=[IO.Path]::GetFullPath($Root)
if (-not $Root.StartsWith((Join-Path $repo '.tmp')+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Relocation validation requires an isolated repository installation.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'installed-version.txt'))) {
    throw 'Provide an existing isolated installation.'
}
. (Join-Path $PSScriptRoot 'workspace.ps1')
. (Join-Path $PSScriptRoot 'test_windows_process_helpers.ps1')
$run=New-WorkspaceRun -Name 'relocation-data'
$destination=Join-Path $run.work 'relocated'
$registry='HKCU:\Software\pdf2zh-relocation-data-'+[guid]::NewGuid().ToString('N')
$env:PDF2ZH_WINDOWS_APP_ROOT=$Root
$env:PDF2ZH_WINDOWS_PORT='18894'
$env:PDF2ZH_WINDOWS_REGISTRY_KEY=$registry
$env:PDF2ZH_WINDOWS_START_MENU_DIR=Join-Path $run.work 'menu'
$env:PDF2ZH_WINDOWS_LIFECYCLE_TEST='1'
$passed=$false
try {
    $packageDirectory=Join-Path $run.work 'package'
    Expand-Archive -LiteralPath (Resolve-Path $Package).Path -DestinationPath $packageDirectory
    $wheelPath=(Resolve-Path $Wheel).Path
    & (Join-Path $packageDirectory 'install.ps1') -PackageSource $wheelPath -GuiSource (Join-Path $packageDirectory 'zotero-pdf2zh-pro.exe') -NonInteractive
    . (Join-Path $Root 'bin/common.ps1')
    $python=Join-Path (Split-Path (Get-ServerExecutable)) 'python.exe'
    $env:PDF2ZH_DATA_DIR=Join-Path $run.work 'runtime-probe'
    & $python (Join-Path $PSScriptRoot 'check_installed_runtime.py') $PackageVersion
    if ($LASTEXITCODE -ne 0) {throw 'Final candidate runtime validation failed.'}
    & $python (Join-Path $PSScriptRoot 'release-data-fixtures.py') seed $DataDir
    if ($LASTEXITCODE -ne 0) {throw 'Cannot seed historical data.'}
    $priorBackup=Join-Path $DataDir 'migration-backup/tasks.json'
    New-Item -ItemType Directory -Force -Path (Split-Path $priorBackup)|Out-Null
    [IO.File]::WriteAllText($priorBackup,'previous migration backup: preserve verbatim')
    & (Join-Path $BinDir 'start-server.ps1') -Quiet
    $relocation=Join-Path $BinDir 'relocate.ps1'
    & (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $relocation -ParentProcessId 2147483647 -SourceRoot $Root -DestinationRoot $destination -CurrentVersion $PackageVersion -PackageSource $wheelPath
    if ($LASTEXITCODE -ne 0) {throw 'Actual installation relocation failed.'}
    $env:PDF2ZH_WINDOWS_APP_ROOT=$destination
    . (Join-Path $destination 'bin/common.ps1')
    $null=Wait-TestControlStartup
    if (-not (Test-ExpectedHealth (Get-ServerHealth))) {throw 'Relocated service is not healthy.'}
    $deadline=[Diagnostics.Stopwatch]::StartNew()
    while ((Test-Path -LiteralPath $Root) -and $deadline.Elapsed.TotalSeconds -lt 600) {Start-Sleep -Milliseconds 500}
    if (Test-Path -LiteralPath $Root) {throw 'The old installation was not removed.'}
    $python=Join-Path (Split-Path (Get-ServerExecutable)) 'python.exe'
    & $python (Join-Path $PSScriptRoot 'release-data-fixtures.py') verify $DataDir
    if ($LASTEXITCODE -ne 0) {throw 'Historical task paths or data broke after removing the old installation.'}
    if ([IO.File]::ReadAllText((Join-Path $DataDir 'migration-backup/tasks.json')) -ne 'previous migration backup: preserve verbatim') {
        throw 'Relocation overwrote an earlier migration backup.'
    }
    $passed=$true
    Write-Host 'Final candidate installation and relocation preserve usable task paths, all data fixtures and earlier backups after deleting the source.'
} finally {
    Stop-IsolatedTestProcesses -Roots @($Root,$run.work)
    if ($registry -notmatch '^HKCU:\\Software\\pdf2zh-relocation-data-[a-f0-9]{32}$') {throw 'Unsafe test registry cleanup.'}
    Remove-Item -LiteralPath $registry -Recurse -Force -ErrorAction SilentlyContinue
    $result=@{status=$(if($passed){'passed'}else{'failed'});packageSha256=(Get-TestFileSha256 $Package);wheelSha256=(Get-TestFileSha256 $Wheel)}
    $result|ConvertTo-Json|Set-Content -Encoding utf8 -LiteralPath (Join-Path $run.report 'validation.json')
    if ($Receipt) {$result|ConvertTo-Json|Set-Content -Encoding utf8 -LiteralPath $Receipt}
    Complete-WorkspaceRun -Run $run -Passed $passed
}

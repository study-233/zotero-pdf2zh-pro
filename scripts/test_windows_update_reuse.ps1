param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)][string]$Report,
    [string]$RegistryKey='HKCU:\Software\pdf2zh-native-isolated-test',
    [string]$StartMenuDirectory)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$Root=[IO.Path]::GetFullPath($Root)
if (-not $Root.StartsWith((Join-Path $repo '.tmp')+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'Reuse tests require a .tmp root.'}
$env:PDF2ZH_WINDOWS_APP_ROOT=$Root
$env:PDF2ZH_WINDOWS_PORT='19890'
$env:PDF2ZH_WINDOWS_REGISTRY_KEY=$RegistryKey
$env:PDF2ZH_WINDOWS_START_MENU_DIR=if ($StartMenuDirectory) {$StartMenuDirectory} else {Join-Path $repo '.tmp/native-test-menu'}
$env:UV_OFFLINE='1'
$bin=Join-Path $Root 'bin'
. (Join-Path $bin 'common.ps1')
. (Join-Path $PSScriptRoot 'test_windows_process_helpers.ps1')
if (-not $AutostartApprovedKey.StartsWith($env:PDF2ZH_WINDOWS_REGISTRY_KEY+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'The test startup approval key was not isolated.'}
function Space {
 $text=& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bin 'clean-cache.ps1') -ReportOnly
 if ($LASTEXITCODE -ne 0) {throw 'Space measurement failed.'}
 $match=[regex]::Match(($text -join ' '),'logicalBytes=(\d+); uniqueFileBytes=(\d+)')
 if (-not $match.Success) {throw 'Missing space result.'}
 return @{logicalBytes=[long]$match.Groups[1].Value;uniqueFileBytes=[long]$match.Groups[2].Value}
}
$baseline=Space
$rows=@()
for ($iteration=1;$iteration -le 3;$iteration++) {
 $clock=[Diagnostics.Stopwatch]::StartNew()
 & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bin 'install.ps1') -PrepareOnly -Repair -NonInteractive
 if ($LASTEXITCODE -ne 0) {throw 'Offline candidate preparation failed.'}
 $prepareMs=$clock.ElapsedMilliseconds
 & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bin 'apply-update.ps1') -ParentProcessId 2147483647 -Prepared -KeepStopped
 if ($LASTEXITCODE -ne 0) {throw 'Prepared runtime switch failed.'}
 $totalMs=$clock.ElapsedMilliseconds
 $null=Wait-TestControlStartup
 if (Get-ListeningProcessId) {throw 'An offline switch restarted the stopped service.'}
 Stop-ManagedControlPanel
 $space=Space
 $rows+=@{iteration=$iteration;preparationMilliseconds=$prepareMs;totalMilliseconds=$totalMs;networkBytes=0;
     logicalBytes=$space.logicalBytes;uniqueFileBytes=$space.uniqueFileBytes;netUniqueBytes=$space.uniqueFileBytes-$baseline.uniqueFileBytes}
}
@{kind='three offline same-version candidate switches';version=$PackageVersion;baseline=$baseline;iterations=$rows}|
 ConvertTo-Json -Depth 6|Set-Content -LiteralPath $Report -Encoding utf8
Write-Host 'Three offline candidate switches reused Python and locked dependencies; stopped state preserved.'

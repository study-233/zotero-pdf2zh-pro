param(
    [Parameter(Mandatory=$true)][string]$PreviousPackage,
    [Parameter(Mandatory=$true)][string]$PreviousWheel,
    [Parameter(Mandatory=$true)][string]$Package,
    [Parameter(Mandatory=$true)][string]$Wheel,
    [string]$Root,
    [string]$DownloadCache
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$temporaryRoot=(Join-Path $repo '.tmp')+'\'
if ($Root) {
    $Root=[IO.Path]::GetFullPath($Root)
    if (-not $Root.StartsWith($temporaryRoot,[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $Root)) {
        throw 'Previous-release migration requires a fresh installation under repository .tmp.'
    }
}
. (Join-Path $PSScriptRoot 'workspace.ps1')
$workspaceRun=New-WorkspaceRun -Name 'migration'
$fixture=$workspaceRun.work
# Keep the managed fixture compact: chardet's native loader still has MAX_PATH
# limits when a repair adds another suffix to the versioned environment path.
if (-not $Root) { $Root=Join-Path $fixture '中文 with spaces' }
$passed=$false
$old=Join-Path $fixture 'old'
$new=Join-Path $fixture 'new'
$registry='HKCU:\Software\pdf2zh-previous-release-'+[guid]::NewGuid().ToString('N')
$menu=Join-Path $fixture 'menu'
$env:PDF2ZH_WINDOWS_APP_ROOT=$Root
$env:PDF2ZH_WINDOWS_PORT='18891'
$env:PDF2ZH_WINDOWS_REGISTRY_KEY=$registry
$env:PDF2ZH_WINDOWS_START_MENU_DIR=$menu
$env:PDF2ZH_WINDOWS_LIFECYCLE_TEST='1'
. (Join-Path $PSScriptRoot 'test_windows_process_helpers.ps1')
Expand-Archive -LiteralPath (Resolve-Path $PreviousPackage).Path -DestinationPath $old
Expand-Archive -LiteralPath (Resolve-Path $Package).Path -DestinationPath $new
$PreviousWheel=(Resolve-Path $PreviousWheel).Path
$Wheel=(Resolve-Path $Wheel).Path

# The published binary and wheel stay intact. v1.8.2 management scripts predate
# registry isolation, so redirect only their two startup-key constants in this fixture.
$oldCommon=Join-Path $old 'common.ps1'
$text=Get-Content -Raw -Encoding utf8 -LiteralPath $oldCommon
foreach ($mapping in @(
    @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',"$registry\Run"),
    @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run',"$registry\StartupApproved\Run")
)) {
    if (-not $text.Contains('"'+$mapping[0]+'"')) {throw 'Previous startup-key fixture no longer matches v1.8.2.'}
    $text=$text.Replace('"'+$mapping[0]+'"','"'+$mapping[1]+'"')
}
Set-Content -LiteralPath $oldCommon -Value $text -Encoding utf8
function Assert-True([bool]$Condition,[string]$Message) {if (-not $Condition) {throw $Message}}
function Wait-Version([string]$Version) {
    for ($attempt=0;$attempt -lt 120;$attempt++) {
        try {
            $request=[Net.WebRequest]::Create('http://127.0.0.1:18891/health')
            $request.Proxy=$null
            $request.Timeout=1000
            $response=$request.GetResponse()
            try {
                $reader=New-Object IO.StreamReader($response.GetResponseStream())
                try {$health=$reader.ReadToEnd()|ConvertFrom-Json} finally {$reader.Dispose()}
            } finally {$response.Dispose()}
            if ($health.version -eq $Version -and $health.workspace.path -eq (Join-Path $Root 'data')) {return}
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    throw "Expected isolated service version $Version did not become healthy."
}
try {
    if ($DownloadCache) {
        $cacheSource=(Resolve-Path -LiteralPath $DownloadCache).Path
        if (-not $cacheSource.StartsWith($temporaryRoot,[StringComparison]::OrdinalIgnoreCase)) {throw 'Only a repository .tmp download cache may be reused.'}
        New-Item -ItemType Directory -Force -Path $Root, (Join-Path $Root 'runtime') | Out-Null
        Copy-Item -LiteralPath (Join-Path $cacheSource 'cache') -Destination (Join-Path $Root 'cache') -Recurse
        Copy-Item -LiteralPath (Join-Path $cacheSource 'runtime/python') -Destination (Join-Path $Root 'runtime/python') -Recurse
    }
    # Recreate a supported pre-5 environment for the unchanged published wheel.
    # v1.9.0 adds this compatibility bound to its own package metadata.
    $constraint=Join-Path $fixture 'previous-constraints.txt'
    Set-Content -LiteralPath $constraint -Encoding ascii -Value 'bitstring==4.4.0'
    $previousConstraint=$env:UV_CONSTRAINT
    try {
        $env:UV_CONSTRAINT=$constraint
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $old 'install.ps1') -PackageSource $PreviousWheel -GuiSource (Join-Path $old 'zotero-pdf2zh-pro.exe') -SkipUvBootstrap -NonInteractive -AllowPreparedDestination
    } finally {$env:UV_CONSTRAINT=$previousConstraint}
    Assert-True ($LASTEXITCODE -eq 0) 'The real v1.8.2 baseline failed to install.'
    $bin=Join-Path $Root 'bin'
    $control=Join-Path $bin 'zotero-pdf2zh-pro.exe'
    New-Item -ItemType Directory -Force -Path "$registry\Run" | Out-Null
    $autostart='"'+$control+'" --autostart'
    New-ItemProperty -LiteralPath "$registry\Run" -Name 'zotero-pdf2zh-pro' -Value $autostart -PropertyType String -Force | Out-Null
    $oldProcess=Start-Process -FilePath $control -ArgumentList '--autostart' -WindowStyle Hidden -PassThru
    Wait-Version '1.8.2'
    $settings=Join-Path $Root 'settings.json'
    Set-Content -LiteralPath $settings -Encoding utf8 -Value '{"autoUpdateDisabled":true,"preservedUpgradeSetting":"keep"}'
    $data=Join-Path $Root 'data'
    foreach ($name in @('preserved-task.txt','preserved-config.json','preserved-glossary.csv')) {
        Set-Content -LiteralPath (Join-Path $data $name) -Encoding utf8 -Value 'Existing v1.8.2 user data'
    }
    $before=@{}
    foreach ($path in @($settings)+(Get-ChildItem -LiteralPath $data -Filter 'preserved-*').FullName) {
        $before[$path]=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $new 'install.ps1') -PackageSource $Wheel -GuiSource (Join-Path $new 'zotero-pdf2zh-pro.exe') -SkipUvBootstrap -PrepareOnly -NonInteractive
    Assert-True ($LASTEXITCODE -eq 0) 'New runtime preparation failed.'
    Assert-True (-not $oldProcess.HasExited) 'Preparation stopped the old control center.'
    Wait-Version '1.8.2'
    # The old server cannot atomically guard streams and requests. Migration must
    # stop until it is explicitly stopped, retaining the verified candidate.
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $new 'apply-update.ps1') -ParentProcessId 2147483647 -Prepared -KeepStopped
    Assert-True ($LASTEXITCODE -ne 0) 'An unguarded live legacy server was replaced.'
    Wait-Version '1.8.2'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bin 'stop-server.ps1') -Quiet
    Assert-True ($LASTEXITCODE -eq 0) 'The idle legacy service did not stop.'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $new 'apply-update.ps1') -ParentProcessId 2147483647 -Prepared -KeepStopped
    Assert-True ($LASTEXITCODE -eq 0) 'Prepared v1.9.0 migration failed.'
    . (Join-Path $bin 'common.ps1')
    $oldProcess.Refresh()
    Assert-True $oldProcess.HasExited 'Migration left the old control center running.'
    Assert-True ((Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim() -eq $PackageVersion) 'Migration lost the installed-version marker.'
    foreach ($path in $before.Keys) {
        Assert-True ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $before[$path]) "Migration changed preserved user data: $path"
    }
    Assert-True ((Get-ItemPropertyValue -LiteralPath "$registry\Run" -Name $ProductName) -eq $autostart) 'Migration changed the startup setting.'
    Assert-True (Test-Path -LiteralPath (Join-Path $Root 'runtime/tools/zotero-pdf2zh-pro/Scripts/zotero-pdf2zh-pro.exe')) 'Migration discarded the legacy runtime before retention expiry.'
    Stop-ManagedControlPanel
    Start-Process -FilePath $ControlPanelExecutable -ArgumentList '--post-install' -WindowStyle Hidden | Out-Null
    Wait-Version $PackageVersion
    $null=Wait-TestControlStartup
    Assert-True ($null -ne (Get-ManagedControlPanelProcessId)) 'New control center did not take over.'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bin 'stop-server.ps1') -Quiet
    Assert-True ($LASTEXITCODE -eq 0) 'New service did not stop.'
    Stop-ManagedControlPanel
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'test_windows_update_reuse.ps1') -Root $Root -Report (Join-Path $Root 'logs/reuse-report.json') -RegistryKey $registry -StartMenuDirectory $menu
    Assert-True ($LASTEXITCODE -eq 0) 'Locked dependency/cache reuse failed.'
    Write-Host 'Real v1.8.2 migration, legacy admission protection, native takeover, retained data/startup and three offline updates passed.'
    $passed=$true
} finally {
    Stop-IsolatedTestProcesses -Roots @($Root, $fixture)
    if ($registry -notlike 'HKCU:\Software\pdf2zh-previous-release-*') {throw 'Unsafe test registry cleanup.'}
    Remove-Item -LiteralPath $registry -Recurse -Force -ErrorAction SilentlyContinue
    Complete-WorkspaceRun -Run $workspaceRun -Passed $passed
}

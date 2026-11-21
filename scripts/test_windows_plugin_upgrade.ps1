# Exercise the plugin's legacy handoff with genuine packages before publishing their URLs.
param([Parameter(Mandatory)][string]$PreviousPackage,[Parameter(Mandatory)][string]$PreviousWheel,
      [Parameter(Mandatory)][string]$Package,[Parameter(Mandatory)][string]$Wheel,
      [Parameter(Mandatory)][string]$Manifest,[string]$Receipt)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'workspace.ps1')
. (Join-Path $PSScriptRoot 'test_windows_process_helpers.ps1')
$run=New-WorkspaceRun -Name 'win-plugin'
$root=Join-Path $run.work '实例'
$registry='HKCU:\Software\pdf2zh-stable-upgrade-'+[guid]::NewGuid().ToString('N')
$env:PDF2ZH_WINDOWS_APP_ROOT=$root
$env:PDF2ZH_WINDOWS_REGISTRY_KEY=$registry
$env:PDF2ZH_WINDOWS_PORT='18894'
$env:PDF2ZH_WINDOWS_START_MENU_DIR=Join-Path $run.work 'menu'
$env:PDF2ZH_WINDOWS_LIFECYCLE_TEST='1'
$result=@{status='failed';stage='install baseline'}
$failure=$null
function Test-ServiceListening {
    $listeners=[Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return [bool]($listeners | Where-Object Port -eq ([int]$env:PDF2ZH_WINDOWS_PORT))
}
try {
    $repo=Split-Path $PSScriptRoot -Parent
    $old=Join-Path $run.work 'old'
    Expand-Archive -LiteralPath $PreviousPackage -DestinationPath $old
    $cache=Join-Path $repo '.tmp/cache/release-test-downloads'
    if(Test-Path -LiteralPath $cache) {
        & python (Join-Path $PSScriptRoot 'release_local.py') seed-cache $cache $root
        if($LASTEXITCODE -ne 0){throw 'Could not seed the installer cache'}
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $old 'install.ps1') -NonInteractive -InstallRoot $root -GuiSource (Join-Path $old 'zotero-pdf2zh-pro.exe') -PackageSource $PreviousWheel -AllowPreparedDestination
    if($LASTEXITCODE -ne 0){throw 'Baseline install failed'}
    $baseline=(Get-Content -Raw -LiteralPath (Join-Path $root 'installed-version.txt')).Trim()
    $official=Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json
    if([version]$baseline -ge [version]$official.version){throw 'The candidate must be newer than the baseline'}
    $bin=Join-Path $root 'bin'
    . (Join-Path $bin 'common.ps1')
    $python=Join-Path (Split-Path (Get-ServerExecutable)) 'python.exe'
    & $python (Join-Path $PSScriptRoot 'release-data-fixtures.py') seed $DataDir
    if($LASTEXITCODE -ne 0){throw 'Could not seed baseline fixtures'}
    if(Test-ServiceListening){throw 'Stopped baseline unexpectedly listening'}
    $snapshot=Get-FileHash -LiteralPath (Join-Path $DataDir 'tasks.json')
    $destination=Join-Path $root 'updates/downloads'
    [void][IO.Directory]::CreateDirectory($destination)
    Copy-Item -LiteralPath $Wheel -Destination $destination
    $result.stage='plugin bootstrap'
    # Only the registry lookup and the two unpublished HTTPS responses are substituted.
    $src=Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $PSScriptRoot 'windows-backend-upgrade.ps1')
    $src=$src.Replace("'HKCU:\Software\zotero-pdf2zh-pro'","'$registry'")
    $src=$src.Replace('Invoke-RestMethod -Uri "$base/windows-update.json" -TimeoutSec 30','Get-Content -Raw -LiteralPath $env:PDF2ZH_TEST_MANIFEST | ConvertFrom-Json')
    $src=$src.Replace('Invoke-WebRequest -UseBasicParsing -Uri $expected -OutFile $zipFile -TimeoutSec 300','Copy-Item -LiteralPath $env:PDF2ZH_TEST_PACKAGE -Destination $zipFile')
    if($src.Contains("'HKCU:\Software\zotero-pdf2zh-pro'") -or $src.Contains('Invoke-WebRequest') -or $src.Contains('Invoke-RestMethod')){throw 'Isolated handoff setup failed'}
    $env:PDF2ZH_TEST_PACKAGE=(Resolve-Path -LiteralPath $Package).Path
    $env:PDF2ZH_TEST_MANIFEST=(Resolve-Path -LiteralPath $Manifest).Path
    $helper=Join-Path $run.work 'handoff.ps1'
    [IO.File]::WriteAllText($helper,$src,(New-Object Text.UTF8Encoding($true)))
    & powershell -NoProfile -ExecutionPolicy Bypass -File $helper -InstallRoot $root -TargetVersion $official.version
    if($LASTEXITCODE -ne 0){throw 'Plugin handoff failed'}
    $result.stage='verify installed candidate'
    . (Join-Path $bin 'common.ps1')
    if((Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim() -ne $official.version){throw 'Wrong installed version'}
    if((Get-SavedInstallRoot) -ine $root){throw 'Registration moved'}
    if(Test-ServiceListening){throw 'Update started a stopped service'}
    if((Get-FileHash -LiteralPath $snapshot.Path).Hash -ne $snapshot.Hash){throw 'Update changed existing tasks'}
    $python=Join-Path (Split-Path (Get-ServerExecutable)) 'python.exe'
    & $python (Join-Path $PSScriptRoot 'release-data-fixtures.py') verify $DataDir
    if($LASTEXITCODE -ne 0){throw 'Candidate cannot read baseline fixtures'}
    $marker=Join-Path $root 'updates/plugin-management.json'
    $deadline=[DateTime]::UtcNow.AddSeconds(50)
    while(!(Test-Path -LiteralPath $marker) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 300}
    $cap=Get-Content -Raw -LiteralPath $marker|ConvertFrom-Json
    if($cap.schema -ne 1 -or $cap.version -ne $official.version){throw 'New control center did not advertise its management interface'}
    $result.status='passed';$result.previousVersion=$baseline;$result.version=$official.version;$result.packageSha256=$official.sha256
} catch {$failure=$_;throw}
finally {Complete-IsolatedUpgrade -Run $run -Roots @($run.work) -Registry $registry -Result $result -Failure $failure -Receipt $Receipt}

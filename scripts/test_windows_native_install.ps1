param([Parameter(Mandatory=$true)][string]$Package,[Parameter(Mandatory=$true)][string]$Wheel,[Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$Root=[IO.Path]::GetFullPath($Root)
if (-not $Root.StartsWith((Join-Path $repo '.tmp')+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'GUI installation tests require an isolated .tmp root.'}
$env:PDF2ZH_WINDOWS_APP_ROOT=$Root
$env:PDF2ZH_WINDOWS_PORT='19890'
$env:PDF2ZH_WINDOWS_REGISTRY_KEY='HKCU:\Software\pdf2zh-native-isolated-test'
$env:PDF2ZH_WINDOWS_START_MENU_DIR=Join-Path $repo '.tmp/native-test-menu'
$source=Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-gui-install-'+[guid]::NewGuid().ToString('N'))
Expand-Archive -LiteralPath (Resolve-Path $Package).Path -DestinationPath $source
Copy-Item -LiteralPath (Resolve-Path $Wheel).Path -Destination (Join-Path $source (Split-Path $Wheel -Leaf))
New-Item -ItemType Directory -Force -Path $Root|Out-Null
@{product='zotero-pdf2zh-pro';root=$Root}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $Root 'install-state.json') -Encoding utf8
. (Join-Path $source 'common.ps1')
if (-not $AutostartApprovedKey.StartsWith($env:PDF2ZH_WINDOWS_REGISTRY_KEY+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'The test startup approval key was not isolated.'}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
function Get-PanelHandle([Diagnostics.Process]$Process) {
 $handle=& python (Join-Path $PSScriptRoot 'check_windows_background.py') $Process.Id --panel
 if ($LASTEXITCODE -ne 0) {throw 'Application window enumeration failed.'}
 return [IntPtr]([long]$handle)
}
function Invoke-SetupButton([Diagnostics.Process]$Process,[string]$Name) {
 $Process.Refresh()
 $window=[Windows.Automation.AutomationElement]::FromHandle((Get-PanelHandle $Process))
 $condition=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty,$Name)
 $button=$window.FindFirst([Windows.Automation.TreeScope]::Descendants,$condition)
 if (-not $button -or -not $button.Current.IsEnabled) {throw "Setup button unavailable: $Name"}
 $invoke=$button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
 $invoke.Invoke()
}
$installer=Start-Process -FilePath (Join-Path $source 'zotero-pdf2zh-pro.exe') -ArgumentList '--setup' -WindowStyle Hidden -PassThru
try {
 Start-Sleep -Seconds 6
 $installer.Refresh()
 if ((Get-PanelHandle $installer) -eq [IntPtr]::Zero) {throw 'First installation panel did not open.'}
 Invoke-SetupButton $installer '开始安装'
 $clock=[Diagnostics.Stopwatch]::StartNew()
 do {
  Start-Sleep -Milliseconds 500
  $progressPath=Join-Path $Root 'updates/operation-progress.json'
  $progress=if(Test-Path -LiteralPath $progressPath){Get-Content -Raw -Encoding utf8 -LiteralPath $progressPath|ConvertFrom-Json}else{$null}
  if($progress -and $progress.status -in @('failed','rolledBack')){throw "Installation failed: $($progress.message)"}
 } while ((-not $progress -or $progress.status -ne 'completed') -and $clock.ElapsedMilliseconds -lt 600000)
 if(-not $progress -or $progress.status -ne 'completed'){throw 'Installation wizard did not complete.'}
 Invoke-SetupButton $installer '打开控制中心'
 do {
  Start-Sleep -Milliseconds 500
  $control=Get-ManagedControlPanelProcessId
  $health=Get-ServerHealth -TimeoutMilliseconds 1000
 } while ((-not $control -or -not (Test-ExpectedHealth $health)) -and $clock.ElapsedMilliseconds -lt 600000)
 if (-not $control -or -not (Test-ExpectedHealth $health)) {throw 'Native first-install handoff did not start the installed service.'}
 if (-not $installer.HasExited -or $control -eq $installer.Id) {throw 'The unpacked installer retained the instance instead of handing off.'}
 $installed=Get-Process -Id $control
 if ((Get-PanelHandle $installed) -eq [IntPtr]::Zero) {throw 'Explicit completion did not open the installed control center.'}
 Write-Host "Native installation wizard, sidecar wheel, explicit completion handoff and original data directory passed; readyMilliseconds=$($clock.ElapsedMilliseconds)."
} finally {
 if (-not $installer.HasExited) {$installer.Kill();$installer.WaitForExit()}
 Stop-ManagedControlPanel
 if (Test-Path -LiteralPath (Join-Path $BinDir 'stop-server.ps1')) {& (Join-Path $BinDir 'stop-server.ps1') -Quiet}
 $resolvedSource=[IO.Path]::GetFullPath($source)
 if ((Split-Path $resolvedSource -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or (Split-Path $resolvedSource -Leaf) -notlike 'pdf2zh-gui-install-*') {throw 'Unsafe test cleanup.'}
 Remove-Item -LiteralPath $resolvedSource -Recurse -Force
}

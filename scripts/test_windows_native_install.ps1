param([Parameter(Mandatory=$true)][string]$Package,[Parameter(Mandatory=$true)][string]$Wheel,[Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$Root=[IO.Path]::GetFullPath($Root)
if (-not $Root.StartsWith((Join-Path $repo '.tmp')+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'GUI installation tests require an isolated .tmp root.'}
$env:PDF2ZH_WINDOWS_APP_ROOT=$Root
$env:PDF2ZH_WINDOWS_PORT='19890'
$testRegistry='HKCU:\Software\pdf2zh-native-isolated-'+[guid]::NewGuid().ToString('N')
$env:PDF2ZH_WINDOWS_REGISTRY_KEY=$testRegistry
$env:PDF2ZH_WINDOWS_START_MENU_DIR=Join-Path (Split-Path $Root -Parent) 'native-test-menu'
$source=Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-gui-install-'+[guid]::NewGuid().ToString('N'))
Expand-Archive -LiteralPath (Resolve-Path $Package).Path -DestinationPath $source
Copy-Item -LiteralPath (Resolve-Path $Wheel).Path -Destination (Join-Path $source (Split-Path $Wheel -Leaf))
New-Item -ItemType Directory -Force -Path $Root|Out-Null
@{product='zotero-pdf2zh-pro';root=$Root}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $Root 'install-state.json') -Encoding utf8
. (Join-Path $source 'common.ps1')
if (-not $AutostartApprovedKey.StartsWith($env:PDF2ZH_WINDOWS_REGISTRY_KEY+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'The test startup approval key was not isolated.'}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
function Get-PanelHandle([Diagnostics.Process]$Process,[switch]$Setup) {
 $arguments=@($Process.Id,'--panel')
 if ($Setup) {$arguments+='--setup'}
 $handle=& python (Join-Path $PSScriptRoot 'check_windows_background.py') @arguments
 if ($LASTEXITCODE -ne 0) {throw 'Application window enumeration failed.'}
 return [IntPtr]([long]$handle)
}
function Invoke-SetupButton([Diagnostics.Process]$Process,[string]$Name) {
 $condition=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty,$Name)
 # The progress file commits before the installer child exits and the UI
 # changes pages. Wait for the actual enabled button, not just that file.
 $deadline=[Diagnostics.Stopwatch]::StartNew()
 do {
  $Process.Refresh()
  if ($Process.HasExited) {throw "Installer exited before button became available: $Name"}
  $handle=Get-PanelHandle $Process -Setup
  if ($handle -ne [IntPtr]::Zero) {
   $window=[Windows.Automation.AutomationElement]::FromHandle($handle)
   $button=$window.FindFirst([Windows.Automation.TreeScope]::Descendants,$condition)
   if (-not $button -and $Name -eq '完成并启动服务') {
    # Keep historical installer validation compatible with its completion label.
    $legacy=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty,'打开控制中心')
    $button=$window.FindFirst([Windows.Automation.TreeScope]::Descendants,$legacy)
   }
   if ($button -and $button.Current.IsEnabled) {
    $invoke=$button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
    $invoke.Invoke()
    return
   }
  }
  Start-Sleep -Milliseconds 500
 } while ($deadline.ElapsedMilliseconds -lt 60000)
 throw "Setup button unavailable: $Name"
}
$setupExecutable=if ($env:PDF2ZH_TEST_INSTALLER) {(Resolve-Path -LiteralPath $env:PDF2ZH_TEST_INSTALLER).Path} else {Join-Path $source 'zotero-pdf2zh-pro.exe'}
$installer=Start-Process -FilePath $setupExecutable -ArgumentList '--setup' -WindowStyle Hidden -PassThru
$bootstrap=$null
try {
 if ($env:PDF2ZH_TEST_INSTALLER) {
  $bootstrap=$installer
  $installer=$null
  for ($attempt=0;$attempt -lt 60;$attempt++) {
   $child=Get-CimInstance Win32_Process -Filter "ParentProcessId=$($bootstrap.Id)" | Where-Object {
    $_.Name -eq 'zotero-pdf2zh-pro.exe' -and $_.CreationDate -ge $bootstrap.StartTime
   } | Select-Object -First 1
   if ($child) {$installer=Get-Process -Id $child.ProcessId;break}
   if ($bootstrap.HasExited) {throw 'The single-file bootstrapper exited before opening the installer.'}
   Start-Sleep -Milliseconds 500
  }
  if (-not $installer) {throw 'The single-file bootstrapper did not launch its installation wizard.'}
 }
 $windowDeadline=[Diagnostics.Stopwatch]::StartNew()
 do {
  $installer.Refresh()
  if ($installer.HasExited) {throw 'Installation wizard exited before opening its panel.'}
  $panel=Get-PanelHandle $installer -Setup
  if ($panel -ne [IntPtr]::Zero) {break}
  Start-Sleep -Milliseconds 500
 } while ($windowDeadline.ElapsedMilliseconds -lt 60000)
 if ($panel -eq [IntPtr]::Zero) {throw 'First installation panel did not open within 60 seconds.'}
 Invoke-SetupButton $installer '开始安装'
 $clock=[Diagnostics.Stopwatch]::StartNew()
 do {
  Start-Sleep -Milliseconds 500
  $progressPath=Join-Path $Root 'updates/operation-progress.json'
  $progress=if(Test-Path -LiteralPath $progressPath){Get-Content -Raw -Encoding utf8 -LiteralPath $progressPath|ConvertFrom-Json}else{$null}
  if($progress -and $progress.status -in @('failed','rolledBack')){throw "Installation failed: $($progress.message)"}
 } while ((-not $progress -or $progress.status -ne 'completed') -and $clock.ElapsedMilliseconds -lt 600000)
 if(-not $progress -or $progress.status -ne 'completed'){throw 'Installation wizard did not complete.'}
 Invoke-SetupButton $installer '完成并启动服务'
 do {
  Start-Sleep -Milliseconds 500
  $control=Get-ManagedControlPanelProcessId
  $health=Get-ServerHealth -TimeoutMilliseconds 1000
 } while ((-not $control -or -not (Test-ExpectedHealth $health)) -and $clock.ElapsedMilliseconds -lt 600000)
 if (-not $control -or -not (Test-ExpectedHealth $health)) {throw 'Native first-install handoff did not start the installed service.'}
 if (-not $installer.HasExited -or $control -eq $installer.Id) {throw 'The unpacked installer retained the instance instead of handing off.'}
 $installed=Get-Process -Id $control
 if ((Get-PanelHandle $installed) -eq [IntPtr]::Zero) {throw 'Explicit completion did not open the installed control center.'}
 if ($bootstrap -and (-not $bootstrap.WaitForExit(30000) -or $bootstrap.ExitCode -ne 0)) {throw 'The single-file bootstrapper did not finish cleanly.'}
 Write-Host "Native installation wizard, sidecar wheel, explicit completion handoff and original data directory passed; readyMilliseconds=$($clock.ElapsedMilliseconds)."
} finally {
 if ($installer -and -not $installer.HasExited) {$installer.Kill();$installer.WaitForExit()}
 if ($bootstrap -and -not $bootstrap.HasExited) {$bootstrap.Kill();$bootstrap.WaitForExit()}
 Stop-ManagedControlPanel
 if (Test-Path -LiteralPath (Join-Path $BinDir 'stop-server.ps1')) {& (Join-Path $BinDir 'stop-server.ps1') -Quiet}
 if ($testRegistry -notmatch '^HKCU:\\Software\\pdf2zh-native-isolated-[a-f0-9]{32}$') {throw 'Unsafe native test registry cleanup.'}
 Remove-Item -LiteralPath $testRegistry -Recurse -Force -ErrorAction SilentlyContinue
 $resolvedSource=[IO.Path]::GetFullPath($source)
 if ((Split-Path $resolvedSource -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or (Split-Path $resolvedSource -Leaf) -notlike 'pdf2zh-gui-install-*') {throw 'Unsafe test cleanup.'}
 Remove-Item -LiteralPath $resolvedSource -Recurse -Force
}

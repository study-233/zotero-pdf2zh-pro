param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][string]$ReportDirectory)
$ErrorActionPreference='Stop'
$executablePath=(Resolve-Path $Executable).Path
$reportPath=[IO.Path]::GetFullPath($ReportDirectory)
New-Item -ItemType Directory -Force -Path $reportPath|Out-Null
$testRoot=Join-Path $reportPath 'ui-install'
$env:PDF2ZH_WINDOWS_APP_ROOT=$testRoot
$env:PDF2ZH_WINDOWS_REGISTRY_KEY='HKCU:\Software\pdf2zh-native-ui-test'
$env:PDF2ZH_WINDOWS_PORT='19891'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NativeUiProbe {
 delegate bool Callback(IntPtr window,IntPtr parameter);
 [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback,IntPtr parameter);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr window,uint message,UIntPtr value,IntPtr parameter);
 public static IntPtr Visible(int process) {
  IntPtr found=IntPtr.Zero;
  EnumWindows(delegate(IntPtr window,IntPtr parameter){uint owner;GetWindowThreadProcessId(window,out owner);if(owner==process&&IsWindowVisible(window)){found=window;return false;}return true;},IntPtr.Zero);
  return found;
 }
}
'@
$process=$null
try {
 $clock=[Diagnostics.Stopwatch]::StartNew()
 $process=Start-Process -FilePath $executablePath -ArgumentList '--candidate' -WindowStyle Hidden -PassThru
 do {Start-Sleep -Milliseconds 50;$window=[NativeUiProbe]::Visible($process.Id)} while ($window -eq [IntPtr]::Zero -and $clock.ElapsedMilliseconds -lt 10000)
 if ($window -eq [IntPtr]::Zero) {throw 'Native panel did not open.'}
 $openedMs=$clock.ElapsedMilliseconds
 Start-Sleep -Seconds 6
 & (Join-Path $PSScriptRoot 'test_windows_window_icons.ps1') -ProcessId $process.Id -Executable $executablePath -ScreenshotPath (Join-Path $reportPath 'native-panel.png')
 [void][NativeUiProbe]::PostMessage($window,0x10,[UIntPtr]::Zero,[IntPtr]::Zero)
 Start-Sleep -Milliseconds 300
 if ($process.HasExited -or [NativeUiProbe]::Visible($process.Id) -ne [IntPtr]::Zero) {throw 'Closing the panel did not retain a background instance.'}
 $duplicate=Start-Process -FilePath $executablePath -ArgumentList '--candidate' -WindowStyle Hidden -PassThru
 if (-not $duplicate.WaitForExit(5000)) {throw 'Duplicate instance did not exit.'}
 Start-Sleep -Milliseconds 300
 if ([NativeUiProbe]::Visible($process.Id) -eq [IntPtr]::Zero) {throw 'Duplicate launch did not reopen the existing panel.'}
 [void][NativeUiProbe]::PostMessage([NativeUiProbe]::Visible($process.Id),0x10,[UIntPtr]::Zero,[IntPtr]::Zero)
 Start-Sleep -Milliseconds 300
 $process.Refresh();$before=$process.TotalProcessorTime.TotalMilliseconds
 Start-Sleep -Seconds 5
 $process.Refresh();$cpu=$process.TotalProcessorTime.TotalMilliseconds-$before
 $memory=$process.WorkingSet64;$private=$process.PrivateMemorySize64
 $children=@(Get-CimInstance Win32_Process|Where-Object {$_.ParentProcessId -eq $process.Id})
 @{openedMilliseconds=$openedMs;workingSetBytes=$memory;privateBytes=$private;cpuMillisecondsOverFiveSeconds=$cpu;childCount=$children.Count;
   sampling='panel closed, five seconds';fixture='uninstalled control center; translation server not started';panelCloseKeepsBackground=$true;duplicateActivatesExisting=$true;os=[Environment]::OSVersion.VersionString}|
   ConvertTo-Json|Set-Content -LiteralPath (Join-Path $reportPath 'resource-report.json') -Encoding utf8
 $handoff=Start-Process -FilePath $executablePath -ArgumentList '--candidate',"--wait-parent=$($process.Id)" -WindowStyle Hidden -PassThru
 Start-Sleep -Milliseconds 200
 if ($handoff.HasExited) {throw 'Installed-instance handoff exited before its parent.'}
 $process.Kill();$process.WaitForExit()
 $handoffClock=[Diagnostics.Stopwatch]::StartNew()
 do {Start-Sleep -Milliseconds 50;$window=[NativeUiProbe]::Visible($handoff.Id)} while ($window -eq [IntPtr]::Zero -and $handoffClock.ElapsedMilliseconds -lt 5000)
 $process=$handoff
 if ($window -eq [IntPtr]::Zero) {throw 'Installed-instance handoff did not open after its parent exited.'}
} finally {
 if ($process -and -not $process.HasExited) {
  if (-not [IO.Path]::GetFullPath($process.Path).Equals($executablePath,[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected test process path.'}
  $process.Kill();$process.WaitForExit()
 }
}

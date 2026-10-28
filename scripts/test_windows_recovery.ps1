Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'workspace.ps1')
$recoveryRun = New-WorkspaceRun -Name 'windows-recovery-lock'
$recoveryPassed = $false
$previousRoot = $env:PDF2ZH_WINDOWS_APP_ROOT
$operation = $null
function Assert-Recovery { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
try {
    $env:PDF2ZH_WINDOWS_APP_ROOT = Join-Path $recoveryRun.work 'product'
    $fixtureBin = Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT 'bin'
    New-Item -ItemType Directory -Force -Path $fixtureBin, (Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT 'updates') | Out-Null
    foreach ($name in @('common.ps1', 'recover-install.ps1')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "windows/$name") -Destination $fixtureBin
    }
    # Stub only rollback and relaunch; exercise the real script and exclusive file lock.
    $runtimeSource = (Join-Path $PSScriptRoot 'windows/runtime.ps1').Replace("'", "''")
    $fixtureRuntime = ". '$runtimeSource'`r`n" + @'
function Restore-RuntimeTransaction {
    param($Transaction)
    if (Test-Path -LiteralPath (Join-Path $AppRoot 'fail-recovery')) { throw 'Injected recovery failure' }
    $Transaction.phase = 'rolledBack'
    Write-AtomicJson (Join-Path $AppRoot 'updates/transaction.json') $Transaction
}
function Start-Process {
    param([string]$FilePath, [string[]]$ArgumentList, [string]$WindowStyle)
    $relaunchLock = Enter-RuntimeOperation
    try { Write-AtomicJson (Join-Path $AppRoot 'restart.json') @{arguments=$ArgumentList;windowStyle=$WindowStyle} }
    finally { $relaunchLock.Dispose() }
}
'@
    Set-Content -LiteralPath (Join-Path $fixtureBin 'runtime.ps1') -Value $fixtureRuntime -Encoding utf8
    . (Join-Path $fixtureBin 'common.ps1')
    . (Join-Path $PSScriptRoot 'windows/runtime.ps1')
    function Invoke-RecoveryFixture {
        param([string]$Case)
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
        $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $fixtureBin 'recover-install.ps1') + '"'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $child = [Diagnostics.Process]::Start($info)
        try {
            $stdout = $child.StandardOutput.ReadToEndAsync()
            $stderr = $child.StandardError.ReadToEndAsync()
            if (-not $child.WaitForExit(15000)) { $child.Kill(); throw "Recovery fixture timed out: $Case" }
            $output = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
            Set-Content -LiteralPath (Join-Path $recoveryRun.report "$Case.log") -Value $output -Encoding utf8
            return $child.ExitCode
        } finally { $child.Dispose() }
    }
    $journal = Join-Path $AppRoot 'updates/transaction.json'
    Assert-Recovery ((Invoke-RecoveryFixture 'no-journal') -eq 0) 'A fresh installation required recovery.'
    Write-AtomicJson $journal @{phase='switching';wasRunning=$false}
    $operation = Enter-RuntimeOperation
    Assert-Recovery ($null -eq (Enter-RuntimeOperation -AllowBusy)) 'A held lock was acquired twice.'
    Assert-Recovery ((Invoke-RecoveryFixture 'lock-busy') -eq 1618) 'Lock contention was not reported as retryable.'
    Assert-Recovery (-not (Test-Path -LiteralPath (Join-Path $AppRoot 'restart.json'))) 'Recovery relaunched while another installer held the lock.'
    Assert-Recovery ((Get-Content -Raw -LiteralPath $journal | ConvertFrom-Json).phase -eq 'switching') 'Recovery modified an active transaction.'
    # The installer finishes while recovery waits. Retry must read the completed journal again.
    Write-AtomicJson $journal @{phase='committed';wasRunning=$true}
    $operation.Dispose(); $operation = $null
    Assert-Recovery ((Invoke-RecoveryFixture 'installer-finished') -eq 0) 'Recovery did not resume after lock release.'
    Assert-Recovery (-not (Test-Path -LiteralPath (Join-Path $AppRoot 'restart.json'))) 'A committed transaction was rolled back or relaunched.'
    foreach ($wasRunning in @($false, $true)) {
        Write-AtomicJson $journal @{phase='switching';wasRunning=$wasRunning}
        Assert-Recovery ((Invoke-RecoveryFixture "interrupted-$wasRunning") -eq 0) 'Interrupted recovery failed or relaunched before releasing its lock.'
        $restart = Get-Content -Raw -LiteralPath (Join-Path $AppRoot 'restart.json') | ConvertFrom-Json
        Assert-Recovery (($restart.arguments -contains '--keep-stopped') -eq (-not $wasRunning)) 'Recovery lost the service stopped state.'
        Assert-Recovery ($restart.windowStyle -eq 'Hidden') 'Recovery launched a visible helper.'
        Assert-Recovery ((Get-Content -Raw -LiteralPath $journal | ConvertFrom-Json).phase -eq 'rolledBack') 'Interrupted recovery was not committed.'
    }
    Remove-Item -LiteralPath (Join-Path $AppRoot 'restart.json') -Force
    Write-AtomicJson $journal @{phase='switching';wasRunning=$true}
    Set-Content -LiteralPath (Join-Path $AppRoot 'fail-recovery') -Value 'fail'
    Assert-Recovery ((Invoke-RecoveryFixture 'recovery-failure') -eq 1) 'A real recovery failure was treated as lock contention.'
    Assert-Recovery (-not (Test-Path -LiteralPath (Join-Path $AppRoot 'restart.json'))) 'Failed recovery relaunched the controller.'
    $operation = Enter-RuntimeOperation
    $operation.Dispose(); $operation = $null
    Remove-Item -LiteralPath (Join-Path $AppRoot '.operation.lock') -Force
    New-Item -ItemType Directory -Path (Join-Path $AppRoot '.operation.lock') | Out-Null
    Assert-Recovery ((Invoke-RecoveryFixture 'invalid-lock-path') -eq 1) 'A non-sharing file error was treated as lock contention.'
    $recoveryPassed = $true
    Write-Host 'Recovery lock contention, retry, relaunch ordering and fatal errors passed.'
} finally {
    if ($operation) { $operation.Dispose() }
    $env:PDF2ZH_WINDOWS_APP_ROOT = $previousRoot
    Complete-WorkspaceRun -Run $recoveryRun -Passed $recoveryPassed
}

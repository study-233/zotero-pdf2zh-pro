Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$directory = Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-runtime-test-' + [guid]::NewGuid().ToString('N'))
$previous = $env:PDF2ZH_WINDOWS_APP_ROOT
$previousCancel = $env:PDF2ZH_INSTALL_CANCEL_FILE
$env:PDF2ZH_WINDOWS_APP_ROOT = $directory
. (Join-Path $PSScriptRoot 'windows/common.ps1')
. (Join-Path $PSScriptRoot 'windows/runtime.ps1')
function Assert-True {param([bool]$Condition,[string]$Message) if (-not $Condition) {throw $Message}}
$previousHealth=[pscustomobject]@{version='1.8.2';workspace=[pscustomobject]@{path=$DataDir;writable=$true}}
Assert-True (Test-ExpectedHealth $previousHealth -ExpectedVersion '1.8.2') 'Owned previous-version health was rejected during maintenance.'
Assert-True (-not (Test-ExpectedHealth $previousHealth -ExpectedVersion '1.0.0')) 'Unexpected installed-version health was accepted.'
$previousHealth.workspace.path=Join-Path $directory 'unrelated'
Assert-True (-not (Test-ExpectedHealth $previousHealth -ExpectedVersion '1.8.2')) 'A foreign workspace was accepted during maintenance.'
function Request-SafeMaintenance {param([int]$IdleSeconds,[switch]$AllowLegacy) return $true}
function Stop-ManagedControlPanel {}
function Update-ProductShellIcons {param($Root,[switch]$NoShortcuts)}
function Save-InstallRoot {param($Path)}
try {
    New-Item -ItemType Directory -Force -Path $BinDir, (Join-Path $AppRoot 'updates'), (Join-Path $RuntimeDir 'versions/new/Scripts') | Out-Null
    $oldServer = Join-Path $RuntimeDir 'versions/old/Scripts/zotero-pdf2zh-pro.exe'
    New-Item -ItemType Directory -Force -Path (Split-Path $oldServer -Parent) | Out-Null
    Set-Content -LiteralPath $oldServer -Value 'old'
    Set-Content -LiteralPath $ExecutableFile -Value $oldServer -Encoding utf8
    Set-Content -LiteralPath $InstalledVersionFile -Value '1.8.1'
    Set-Content -LiteralPath (Join-Path $BinDir 'start-server.ps1') -Value 'Set-Content -LiteralPath (Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT "restored.txt") -Value old; $global:LASTEXITCODE=0'
    Set-Content -LiteralPath (Join-Path $BinDir 'stop-server.ps1') -Value '$global:LASTEXITCODE=0'
    $candidate = Join-Path $AppRoot 'updates/candidates/test/bin'
    New-Item -ItemType Directory -Force -Path $candidate | Out-Null
    Set-Content -LiteralPath (Join-Path $candidate 'start-server.ps1') -Value 'throw "Injected unhealthy candidate"'
    Set-Content -LiteralPath (Join-Path $BinDir 'LICENSE.txt') -Value 'old license'
    Set-Content -LiteralPath (Join-Path $candidate 'LICENSE.txt') -Value 'candidate license'
    Set-Content -LiteralPath (Join-Path $candidate 'THIRD_PARTY_NOTICES.md') -Value 'candidate notices'
    Copy-Item -LiteralPath (Join-Path $BinDir 'stop-server.ps1') -Destination $candidate
    $environment = Join-Path $RuntimeDir 'versions/new'
    Set-Content -LiteralPath (Join-Path $environment 'Scripts/zotero-pdf2zh-pro.exe') -Value 'new'
    Write-AtomicJson (Join-Path $AppRoot 'updates/prepared.json') @{version='1.8.2';environment=$environment;bin=$candidate}
    $env:PDF2ZH_INSTALL_CANCEL_FILE=Join-Path $AppRoot 'cancel.signal'
    Set-Content -LiteralPath $env:PDF2ZH_INSTALL_CANCEL_FILE -Value 'cancel'
    $cancelled=$false
    try {Apply-PreparedRuntime -NoShortcuts} catch [OperationCanceledException] {$cancelled=$true}
    Assert-True $cancelled 'Cancellation before switching was ignored.'
    Assert-True ((Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim() -eq '1.8.1') 'Cancellation changed the installed version.'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $AppRoot 'updates/transaction.json'))) 'Cancellation entered the live-runtime transaction.'
    Remove-Item -LiteralPath $env:PDF2ZH_INSTALL_CANCEL_FILE -Force
    $operation = Enter-RuntimeOperation
    try {
        $blocked=$false
        try { $other=Enter-RuntimeOperation; $other.Dispose() } catch {$blocked=$true}
        Assert-True $blocked 'Concurrent operations were accepted.'
        $failed=$false
        try {Apply-PreparedRuntime -NoShortcuts} catch {Write-Host "Fault result: $_"; $failed=$_.Exception.Message -match 'Injected unhealthy'}
        Assert-True $failed 'Unhealthy candidate did not fail.'
        Assert-True ((Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim() -eq '1.8.1') 'Previous version was not restored.'
        Assert-True ((Get-Content -Raw -Encoding utf8 -LiteralPath $ExecutableFile).Trim() -eq $oldServer) 'Local environment pointer was not restored.'
        Assert-True (Test-Path -LiteralPath (Join-Path $AppRoot 'restored.txt')) 'Previously running service was not restored.'
        $transaction = Get-Content -Raw -LiteralPath (Join-Path $AppRoot 'updates/transaction.json') | ConvertFrom-Json
        Assert-True ($transaction.phase -eq 'rolledBack') 'Rollback journal was not committed.'
        Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $BinDir 'LICENSE.txt')).Trim() -eq 'old license') 'Rollback lost the local license.'
        $transaction.phase='switching'
        Set-Content -LiteralPath $InstalledVersionFile -Value '1.8.2'
        Restore-RuntimeTransaction $transaction
        Assert-True ((Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim() -eq '1.8.1') 'Interrupted replacement did not recover.'
        Set-Content -LiteralPath (Join-Path $candidate 'start-server.ps1') -Value 'Set-Content -LiteralPath $env:PDF2ZH_INSTALL_CANCEL_FILE -Value late-cancel; $global:LASTEXITCODE=0'
        Apply-PreparedRuntime -NoShortcuts -KeepStopped
        $transaction=Get-Content -Raw -LiteralPath (Join-Path $AppRoot 'updates/transaction.json')|ConvertFrom-Json
        Assert-True ($transaction.phase -eq 'committed') 'Healthy candidate was not committed.'
        Assert-True (Test-Path -LiteralPath $env:PDF2ZH_INSTALL_CANCEL_FILE) 'Late cancellation fixture did not run.'
        Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $BinDir 'LICENSE.txt')).Trim() -eq 'candidate license') 'Commit did not copy the license.'
        Assert-True ((Get-Content -Raw -LiteralPath (Join-Path $BinDir 'THIRD_PARTY_NOTICES.md')).Trim() -eq 'candidate notices') 'Commit did not copy third-party notices.'
        Assert-True (-not $transaction.wasRunning) 'Stopped service state was lost.'
    } finally {$operation.Dispose()}
    $escaped=$false
    try {Assert-ProductChild (Join-Path $AppRoot '../unrelated') $AppRoot|Out-Null} catch {$escaped=$true}
    Assert-True $escaped 'Cleanup accepted a path outside the product.'
    Write-Host 'Local rollback, interruption recovery, stopped-state preservation and operation/path guards passed.'
} finally {
    $env:PDF2ZH_WINDOWS_APP_ROOT = $previous
    $env:PDF2ZH_INSTALL_CANCEL_FILE = $previousCancel
    $resolved=[IO.Path]::GetFullPath($directory)
    if ((Split-Path $resolved -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or (Split-Path $resolved -Leaf) -notlike 'pdf2zh-runtime-test-*') {throw 'Unsafe test cleanup path.'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
& (Join-Path $PSScriptRoot 'test_windows_recovery.ps1')

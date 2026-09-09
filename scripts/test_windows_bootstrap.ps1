Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'windows\prepare-webview2.ps1'
. $source -FunctionsOnly

function Assert-True {
    param([bool]$Value, [string]$Message)
    if (-not $Value) { throw $Message }
}

foreach ($version in @('', '0.0.0.0', 'broken', '1..2')) {
    Assert-True (-not (Test-WebView2Runtime -ReadVersion { param($Path) $version })) "Invalid version accepted: $version"
}
Assert-True (-not (Test-WebView2Runtime -ReadVersion { throw 'No registry value' })) 'Missing registration accepted'
Assert-True (Test-WebView2Runtime -ReadVersion {
    param($Path)
    if ($Path -eq 'HKCU:\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') { return '128.0.2739.42' }
    throw 'Missing machine installation'
}) 'Per-user runtime not detected'
Assert-True (Test-WebView2Runtime -ReadVersion {
    param($Path)
    if ($Path -eq 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') { return '128.0.2739.42' }
    throw 'Missing user installation'
}) 'Per-machine runtime not detected'

foreach ($signature in @(
    @{ Status = 'NotSigned'; SignerCertificate = $null },
    @{ Status = 'HashMismatch'; SignerCertificate = @{ Subject = 'O=Microsoft Corporation' } },
    @{ Status = 'Valid'; SignerCertificate = @{ Subject = 'O=Someone Else' } },
    @{ Status = 'Valid'; SignerCertificate = @{ Subject = 'O=Microsoft Corporation Fake' } }
)) {
    $rejected = $false
    try { Assert-MicrosoftSignature $signature } catch { $rejected = $true }
    Assert-True $rejected 'Invalid publisher/signature accepted'
}
Assert-MicrosoftSignature @{ Status = 'Valid'; SignerCertificate = @{ Subject = 'CN=Microsoft Corporation, O=Microsoft Corporation, C=US' } }

$directory = Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-bootstrap-test-' + [guid]::NewGuid().ToString('N'))
$logPath = Join-Path $directory 'setup.log'
try {
    $state = @{ Status = 'working'; Message = ''; Installer = $null }
    Invoke-WebView2Preparation $state $logPath -Detect { $true } -Download { throw 'Must not download' }
    Assert-True ($state.Status -eq 'ready') 'Existing runtime caused installation'

    foreach ($failure in @('download', 'signature', 'installer')) {
        $state = @{ Status = 'working'; Message = ''; Installer = $null }
        $script:installed = $false
        Invoke-WebView2Preparation $state $logPath -Detect { $false } -Download {
            param($Path, $Progress)
            if ($failure -eq 'download') { throw 'Network timeout' }
        } -Verify {
            param($Path)
            if ($failure -eq 'signature') { throw 'Invalid Microsoft signature' }
        } -Install {
            param($Path, $Progress)
            $script:installed = $true
            throw 'Installation failed or timed out'
        }
        Assert-True ($state.Status -eq 'failed') "Failure was ignored: $failure"
        Assert-True ($script:installed -eq ($failure -eq 'installer')) 'Installer ran before download/signature verification'
    }

    $script:runtimeReady = $false
    $state = @{ Status = 'working'; Message = ''; Installer = $null }
    Invoke-WebView2Preparation $state $logPath -Detect { $script:runtimeReady } -Download {} -Verify {} -Install { $script:runtimeReady = $true }
    Assert-True ($state.Status -eq 'ready') 'Successful installation did not continue startup'

    $state = @{ Status = 'working'; Message = ''; Installer = $null }
    Invoke-WebView2Preparation $state $logPath -Detect { $false } -Download {} -Verify {} -Install {} -Pause {}
    Assert-True ($state.Status -eq 'failed') 'Successful installer exit without Runtime was accepted'

    $name = 'Local\pdf2zh-mutex-test-' + [guid]::NewGuid().ToString('N')
    $mutex = [Threading.Mutex]::new($false, $name)
    Assert-True (Enter-PreparationMutex $mutex) 'Cannot acquire preparation mutex'
    $worker = [PowerShell]::Create()
    try {
        [void]$worker.AddScript({
            param($Source, $Name)
            . $Source -FunctionsOnly
            $other = [Threading.Mutex]::new($false, $Name)
            try {
                $acquired = Enter-PreparationMutex $other
                if ($acquired) { $other.ReleaseMutex() }
                return $acquired
            } finally { $other.Dispose() }
        }).AddArgument($source).AddArgument($name)
        $results = $worker.Invoke()
        Assert-True ($results.Count -eq 1 -and -not [bool]$results[0]) 'Duplicate launch acquired preparation mutex'
    } finally {
        $worker.Dispose()
        $mutex.ReleaseMutex()
        $mutex.Dispose()
    }
    Assert-True (Test-Path -LiteralPath $logPath) 'Setup diagnostics were not written'
} finally {
    Remove-Item -LiteralPath $directory -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host 'WebView2 detection, signature, failure recovery, and duplicate-launch tests passed.'

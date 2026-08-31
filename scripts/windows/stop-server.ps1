param([switch]$Quiet)

. (Join-Path $PSScriptRoot "common.ps1")
Assert-WindowsX64

$serverExecutable = Get-ServerExecutable
$managedPid = $null
if (Test-Path -LiteralPath $PidFile) {
    $rawPid = (Get-Content -Raw -LiteralPath $PidFile).Trim()
    if ($rawPid -match "^\d+$") {
        $managedPid = [int]$rawPid
    }
}
if (-not $managedPid) {
    $managedPid = Get-ListeningProcessId
}

if (-not $managedPid) {
    Remove-ManagedProcessState
    Write-Status "zotero-pdf2zh-pro is not running." -Quiet:$Quiet
    exit 0
}
if (
    -not $serverExecutable -or
    -not (Test-ExpectedServerProcess -ProcessId $managedPid -ServerExecutable $serverExecutable)
) {
    throw (
        "Process $managedPid is not the managed zotero-pdf2zh-pro executable. " +
        "It was not stopped."
    )
}

Stop-Process -Id $managedPid
$stopped = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if (-not (Get-Process -Id $managedPid -ErrorAction SilentlyContinue)) {
        $stopped = $true
        break
    }
    Start-Sleep -Milliseconds 250
}
if (-not $stopped) {
    throw "The managed server did not stop within 10 seconds."
}
Remove-ManagedProcessState
Write-Status "zotero-pdf2zh-pro stopped." -Quiet:$Quiet

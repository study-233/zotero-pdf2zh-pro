param([switch]$Quiet)
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'runtime.ps1')
$journal = Join-Path $AppRoot 'updates/transaction.json'
if (-not (Test-Path -LiteralPath $journal)) { exit 0 }
$operation = Enter-RuntimeOperation
try {
    $transaction = Get-Content -Raw -Encoding utf8 -LiteralPath $journal | ConvertFrom-Json
    if ($transaction.phase -notin @('committed', 'rolledBack')) {
        Write-RuntimeLog 'Recovering an interrupted local transaction.'
        Restore-RuntimeTransaction $transaction
        Set-Content -LiteralPath (Join-Path $AppRoot 'last-operation-error.txt') -Value 'Interrupted update recovered locally; check logs before retrying.' -Encoding utf8
        $arguments=@('--post-install'); if (-not $transaction.wasRunning) {$arguments+='--keep-stopped'}
        Start-Process -FilePath $ControlPanelExecutable -ArgumentList $arguments -WindowStyle Hidden
    }
} finally { $operation.Dispose() }

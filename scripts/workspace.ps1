# Local developer outputs only; never use the installed product's cleanup helpers.
$script:workspaceManager = Join-Path $PSScriptRoot 'workspace.py'
function Invoke-MeasuredOperation {
    param([Parameter(Mandatory=$true)][string]$Name, [Parameter(Mandatory=$true)][scriptblock]$Action, [hashtable]$Details=@{})
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $result = @{operation=$Name;started=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000;pid=$PID;status='failed'}
    if ($Details.Count) {$result.details=$Details}
    try {
        & $Action
        $result.status = 'passed'
    } finally {
        $timer.Stop()
        $result.seconds = $timer.Elapsed.TotalSeconds
        if ($env:PDF2ZH_RELEASE_METRICS_DIR) {
            try {
                $null = [IO.Directory]::CreateDirectory($env:PDF2ZH_RELEASE_METRICS_DIR)
                $path = Join-Path $env:PDF2ZH_RELEASE_METRICS_DIR "powershell-$PID.jsonl"
                [IO.File]::AppendAllText($path, (($result | ConvertTo-Json -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
            } catch {Write-Warning "Cannot record release timing: $_"}
        }
        Write-Host ("{0}: {1}; elapsed {2:F2}s" -f $Name,$result.status,$result.seconds)
    }
}
function Invoke-WorkspaceManager {
    param([string[]]$Arguments)
    # Python reports cleanup progress on stderr. Windows PowerShell 5.1 treats
    # redirected native stderr as errors; the process exit code is authoritative.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $text = & python -B -X utf8 $script:workspaceManager @Arguments
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($exitCode -ne 0) { throw "Workspace manager failed: $($Arguments[0])" }
    return ($text -join "`n" | ConvertFrom-Json)
}
function New-WorkspaceRun {
    param([Parameter(Mandatory=$true)][string]$Name)
    return Invoke-WorkspaceManager -Arguments @('start', $Name, '--owner-pid', [string]$PID)
}
function New-WorkspaceReport {
    param([Parameter(Mandatory=$true)][string]$Name)
    return (Invoke-WorkspaceManager -Arguments @('report', $Name, '--owner-pid', [string]$PID)).report
}
function Complete-WorkspaceRun {
    param([Parameter(Mandatory=$true)]$Run, [bool]$Passed=$false)
    $status = if ($Passed) { 'passed' } else { 'failed' }
    try {
        $result = Invoke-WorkspaceManager -Arguments @('finish', $Run.work, '--status', $status)
        Write-Host "Validation evidence: $($result.report)"
    } catch {
        Write-Warning "Temporary workspace retained at $($Run.work): $($_.Exception.Message)"
    }
}

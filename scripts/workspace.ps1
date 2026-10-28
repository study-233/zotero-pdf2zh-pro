# Local developer outputs only; never use the installed product's cleanup helpers.
$script:workspaceManager = Join-Path $PSScriptRoot 'workspace.py'
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

param(
    [Parameter(Mandatory = $true)][int]$ParentProcessId,
    [string]$PackageSource,
    [string]$InstallRoot,
    [switch]$Prepared,
    [switch]$KeepStopped
)
$inheritedInstallRootOverride = -not [string]::IsNullOrWhiteSpace($env:PDF2ZH_WINDOWS_APP_ROOT)
if ($InstallRoot) {
    if (-not [IO.Path]::IsPathRooted($InstallRoot)) { throw 'An absolute installation path is required.' }
    $env:PDF2ZH_WINDOWS_APP_ROOT = [IO.Path]::GetFullPath($InstallRoot)
}
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'runtime.ps1')
$wasStopped = $KeepStopped -or -not (Get-ListeningProcessId)
try {
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        if (-not (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) { throw 'Previous control center did not exit.' }
    if (-not $Prepared) {
        $arguments = @{PrepareOnly=$true;NonInteractive=$true;InstallRoot=$AppRoot;GuiSource=(Join-Path $PSScriptRoot "$ProductName.exe");DeferLocationCommit=$inheritedInstallRootOverride}
        if ($PackageSource) {$arguments.PackageSource=$PackageSource}
        & (Join-Path $PSScriptRoot 'install.ps1') @arguments
        if ($LASTEXITCODE -ne 0) {throw 'Candidate preparation failed.'}
    }
    $operation = Enter-RuntimeOperation
    try { Apply-PreparedRuntime -KeepStopped:$wasStopped -DeferLocationCommit:$inheritedInstallRootOverride }
    finally { $operation.Dispose() }
    $arguments = @('--post-install')
    if ($wasStopped) { $arguments += '--keep-stopped' }
    if (-not $inheritedInstallRootOverride) {$env:PDF2ZH_WINDOWS_APP_ROOT=$null}
    Start-Process -FilePath $ControlPanelExecutable -ArgumentList $arguments -WindowStyle Hidden
} catch {
    Write-RuntimeLog "Update failed: $_"
    Set-Content -LiteralPath (Join-Path $AppRoot 'last-operation-error.txt') -Value $_.Exception.Message -Encoding utf8
    if ((Test-Path -LiteralPath $ControlPanelExecutable) -and -not (Get-ManagedControlPanelProcessId)) {
        $arguments = @('--post-install'); if ($wasStopped) {$arguments += '--keep-stopped'}
        if (-not $inheritedInstallRootOverride) {$env:PDF2ZH_WINDOWS_APP_ROOT=$null}
        Start-Process -FilePath $ControlPanelExecutable -ArgumentList $arguments -WindowStyle Hidden
    }
    exit 1
}

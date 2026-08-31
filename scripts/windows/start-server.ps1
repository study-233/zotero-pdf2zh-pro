param([switch]$Quiet)

. (Join-Path $PSScriptRoot "common.ps1")
Assert-WindowsX64

$serverExecutable = Get-ServerExecutable
if (-not $serverExecutable) {
    throw "The server is not installed. Run install.cmd first."
}

$listenerPid = Get-ListeningProcessId
if ($listenerPid) {
    $health = Get-ServerHealth
    if (
        (Test-ExpectedServerProcess -ProcessId $listenerPid -ServerExecutable $serverExecutable) -and
        (Test-ExpectedHealth -Health $health)
    ) {
        Save-ManagedProcess -ProcessId $listenerPid -ServerExecutable $serverExecutable
        Write-Status "zotero-pdf2zh-pro is already running." -Quiet:$Quiet
        exit 0
    }
    throw "Port $ServerPort is already used by another process. No process was stopped."
}

New-Item -ItemType Directory -Force -Path $DataDir, $LogsDir | Out-Null
$arguments = (
    '--host "{0}" --port {1} --data-dir "{2}" --log-file "{3}"' -f
    $ServerHost,
    $ServerPort,
    $DataDir.Replace('"', '\"'),
    $LogFile.Replace('"', '\"')
)
$process = Start-Process -FilePath $serverExecutable -ArgumentList $arguments -WindowStyle Hidden -PassThru
Save-ManagedProcess -ProcessId $process.Id -ServerExecutable $serverExecutable

try {
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) {
            throw "The server exited during startup. See $LogFile"
        }
        $health = Get-ServerHealth
        if (Test-ExpectedHealth -Health $health) {
            $activePid = Get-ListeningProcessId
            if (
                -not $activePid -or
                -not (Test-ExpectedServerProcess -ProcessId $activePid -ServerExecutable $serverExecutable)
            ) {
                throw "The healthy endpoint is not owned by the installed server."
            }
            Save-ManagedProcess -ProcessId $activePid -ServerExecutable $serverExecutable
            Write-Status "zotero-pdf2zh-pro started at $HealthUrl" -Quiet:$Quiet
            exit 0
        }
        if ($health -and $health.version -ne $PackageVersion) {
            throw (
                "Server version {0} does not match package version {1}." -f
                $health.version,
                $PackageVersion
            )
        }
    }
    throw "The server did not become healthy. See $LogFile"
} catch {
    $activePid = Get-ListeningProcessId
    if (
        $activePid -and
        (Test-ExpectedServerProcess -ProcessId $activePid -ServerExecutable $serverExecutable)
    ) {
        Stop-Process -Id $activePid -Force -ErrorAction SilentlyContinue
    }
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-ManagedProcessState
    throw
}

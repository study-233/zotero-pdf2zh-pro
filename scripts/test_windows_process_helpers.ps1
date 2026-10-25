# Shared by real-package tests; never select a process by its name alone.
function Get-TestFileSha256 {
    param([string]$Path)
    $stream=[IO.File]::OpenRead($Path)
    $algorithm=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}

function Complete-IsolatedUpgrade {
    param($Run, [string[]]$Roots, [string]$Registry, [hashtable]$Result, $Failure)
    if ($Failure) { $Result.failure=$Failure.ToString(); $Result.stack=$Failure.ScriptStackTrace }
    $cleanupFailure=$null
    try {
        Stop-IsolatedTestProcesses -Roots $Roots
        if ($Registry -notmatch '^HKCU:\\Software\\pdf2zh-(stable-upgrade|previous-release)-[a-f0-9]{32}$') {
            throw 'Unsafe upgrade-test registry cleanup.'
        }
        Remove-Item -LiteralPath $Registry -Recurse -Force -ErrorAction SilentlyContinue
    } catch { $cleanupFailure=$_; $Result.status='failed'; $Result.cleanupFailure=$_.ToString() }
    try {
        $Result|ConvertTo-Json -Depth 6|Set-Content -Encoding utf8 -LiteralPath (Join-Path $Run.report 'validation.json')
    } catch {
        if (-not $Failure) { throw }
        Write-Warning "Cannot write upgrade receipt: $_"
    }
    if ($cleanupFailure) {
        Write-Warning "Test workspace retained: $cleanupFailure"
        if (-not $Failure) { throw $cleanupFailure }
    } else { Complete-WorkspaceRun -Run $Run -Passed ($Result.status -eq 'passed') }
}

function Stop-IsolatedTestProcesses {
    param([Parameter(Mandatory=$true)][AllowEmptyString()][string[]]$Roots)
    $allowed = @((Join-Path ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))) '.tmp'), [IO.Path]::GetTempPath())
    if ($env:RUNNER_TEMP) { $allowed += $env:RUNNER_TEMP }
    $prefixes = @($Roots | Where-Object { $_ } | ForEach-Object {
        $resolved = [IO.Path]::GetFullPath($_).TrimEnd('\') + '\'
        if (-not @($allowed | Where-Object {
            $resolved.StartsWith([IO.Path]::GetFullPath($_).TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -and
            $resolved -ine ([IO.Path]::GetFullPath($_).TrimEnd('\') + '\')
        }).Count) { throw "Unsafe test process cleanup root: $resolved" }
        $resolved
    })
    # Workers may spawn a service while their parent is being stopped.
    for ($pass = 0; $pass -lt 4; $pass++) {
        $owned = @(Get-CimInstance Win32_Process | Where-Object {
            $candidate = $_
            $scriptPath = ''
            if ($candidate.Name -in @('powershell.exe', 'pwsh.exe') -and
                $candidate.CommandLine -match '(?i)\s-File\s+(?:"([^"]+)"|(\S+))') {
                $scriptPath = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
            }
            $cleanupRoot = ''
            if ($scriptPath -and (Split-Path $scriptPath -Leaf) -match '^zotero-pdf2zh-pro-(?:relocate-)?cleanup-[a-f0-9]{32}\.ps1$' -and
                $candidate.CommandLine -match '(?i)\s-(?:RootPath|SourcePath)\s+"([^"]+)"') {
                $cleanupRoot = [IO.Path]::GetFullPath($Matches[1]).TrimEnd('\') + '\'
            }
            @($prefixes | Where-Object {
                ($candidate.ExecutablePath -and $candidate.ExecutablePath.StartsWith($_, [StringComparison]::OrdinalIgnoreCase)) -or
                ($scriptPath -and $scriptPath.StartsWith($_, [StringComparison]::OrdinalIgnoreCase)) -or
                ($cleanupRoot -and $cleanupRoot -ieq $_)
            }).Count -gt 0
        } | Sort-Object @{Expression={if ($_.Name -in @('powershell.exe','pwsh.exe')) {0} else {1}}})
        if (-not $owned.Count) { return }
        foreach ($candidate in $owned) {
            $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($candidate.ProcessId)"
            if ($current -and $current.CreationDate -eq $candidate.CreationDate -and $current.ExecutablePath -eq $candidate.ExecutablePath) {
                Stop-Process -Id $current.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'Isolated test processes did not stop after bounded cleanup.'
}

function Get-TestControlStartupChildren {
    param([int]$ControlProcessId)
    $controller = Get-CimInstance Win32_Process -Filter "ProcessId=$ControlProcessId"
    if (-not $controller) { return }
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ControlProcessId" | Where-Object {
        # Windows can reuse an exited parent's PID; older processes are not
        # children of this controller even if their numeric parent ID matches.
        $_.Name -ieq 'powershell.exe' -and $_.CreationDate -ge $controller.CreationDate
    }
}

function Wait-TestControlStartup {
    # A PID marker precedes recovery/startup completion. Wait for its PowerShell
    # children before stopping the service or starting another transaction.
    $quietSamples = 0
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while ($deadline.ElapsedMilliseconds -lt 90000) {
        $controlProcessId = Get-ManagedControlPanelProcessId
        if ($controlProcessId) {
            $children = @(Get-TestControlStartupChildren -ControlProcessId $controlProcessId)
            if (-not $children.Count) {
                $quietSamples++
                if ($quietSamples -ge 4) { return $controlProcessId }
            } else { $quietSamples = 0 }
        } else { $quietSamples = 0 }
        Start-Sleep -Milliseconds 500
    }
    throw 'The isolated control center did not finish startup.'
}

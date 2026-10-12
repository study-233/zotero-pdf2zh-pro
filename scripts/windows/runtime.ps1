# Candidate environments have permanent paths; virtual environments must never be renamed.
$script:InstallCommitStarted = $false
function Assert-InstallNotCancelled {
    if (-not $script:InstallCommitStarted -and $env:PDF2ZH_INSTALL_CANCEL_FILE -and
        (Test-Path -LiteralPath $env:PDF2ZH_INSTALL_CANCEL_FILE -PathType Leaf)) {
        throw [OperationCanceledException]::new('安装已取消')
    }
}
function Wait-InstallTask {
    param($Task)
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not $Task.IsCompleted) {
        Assert-InstallNotCancelled
        if ($clock.Elapsed.TotalSeconds -ge 30) { throw [TimeoutException]::new('Download timed out.') }
        Start-Sleep -Milliseconds 100
    }
    Assert-InstallNotCancelled
    return $Task.GetAwaiter().GetResult()
}
function Write-AtomicJson {
    param([string]$Path, $Value)
    $temporary = "$Path.$PID.tmp"
    $bytes=(New-Object Text.UTF8Encoding($false)).GetBytes(($Value | ConvertTo-Json -Depth 12))
    $stream=[IO.File]::Open($temporary,'Create','Write','None')
    try {$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)} finally {$stream.Dispose()}
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, "$Path.previous.tmp"); Remove-Item -LiteralPath "$Path.previous.tmp" -Force }
    else { [IO.File]::Move($temporary, $Path) }
}

function Enter-RuntimeOperation {
    New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null
    try { return [IO.File]::Open((Join-Path $AppRoot '.operation.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another installation, update, migration or cleanup is running.' }
}

function Assert-ProductChild {
    param([string]$Path, [string]$Parent)
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $prefix = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe product path: $Path" }
    $cursor = $full
    while ($cursor.Length -ge $prefix.Length) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -Force -LiteralPath $cursor).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Product paths cannot contain junctions: $cursor"
        }
        $cursor = Split-Path $cursor -Parent
    }
    return $full
}

function Write-RuntimeLog {
    param([string]$Message)
    New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
    Rotate-ProductLog -Path $ControlLogFile
    Add-Content -LiteralPath $ControlLogFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [runtime] $Message" -Encoding utf8
    Write-Host $Message
}

function Write-OperationProgress {
    param([string]$Phase, [string]$Message, [string]$Status = 'running',
        [Nullable[long]]$Completed = $null, [Nullable[long]]$Total = $null, [string]$Unit = '')
    if ($Status -in @('running','waiting') -and $Phase -ne 'rollback') { Assert-InstallNotCancelled }
    $progressPath = Join-Path $AppRoot 'updates/operation-progress.json'
    $previous = $null
    if (Test-Path -LiteralPath $progressPath) {
        try { $previous = Get-Content -Raw -Encoding utf8 -LiteralPath $progressPath | ConvertFrom-Json } catch {}
    }
    $epoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $active = $previous -and $previous.status -in @('running', 'waiting')
    $value = @{ operationId = if ($active) {$previous.operationId} else {[guid]::NewGuid().ToString('N')};
        operation = if ($active) {$previous.operation} else {'install'};
        startedAt = if ($active) {$previous.startedAt} else {$epoch}; updatedAt = $epoch;
        sequence = if ($active -and $previous.PSObject.Properties['sequence']) {[long]$previous.sequence + 1} else {1};
        files = @(if ($active -and $previous.PSObject.Properties['files']) {$previous.files});
        recentLogs = @(if ($active -and $previous.PSObject.Properties['recentLogs']) {$previous.recentLogs});
        progressUnavailable = $false;
        phase = $Phase; status = $Status; message = $Message;
        completed = $Completed; total = $Total; unit = if ($Unit) {$Unit} else {$null} }
    Write-AtomicJson $progressPath $value
    [Console]::Out.WriteLine('PDF2ZH_PROGRESS ' + ($value | ConvertTo-Json -Compress -Depth 12))
}

function Save-ProgressDownload {
    param([string]$Uri, [string]$Destination, [string]$Message)
    $request = [Net.HttpWebRequest]::Create($Uri)
    $request.Timeout = 30000; $request.ReadWriteTimeout = 30000
    $response = $null; $stream = $null; $file = $null
    try {
        Assert-InstallNotCancelled
        $response = Wait-InstallTask ($request.GetResponseAsync()); $stream = $response.GetResponseStream()
        $total = if ($response.ContentLength -gt 0) {[Nullable[long]]$response.ContentLength} else {$null}
        $file = [IO.File]::Open($Destination, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $buffer = New-Object byte[] 65536; $received = [long]0
        $clock = [Diagnostics.Stopwatch]::StartNew(); $last = [long]0
        Write-OperationProgress 'download' $Message -Completed 0 -Total $total -Unit 'bytes'
        while (($count = Wait-InstallTask ($stream.ReadAsync($buffer, 0, $buffer.Length))) -gt 0) {
            $file.Write($buffer, 0, $count); $received += $count
            if ($clock.ElapsedMilliseconds - $last -ge 500) {
                Write-OperationProgress 'download' $Message -Completed $received -Total $total -Unit 'bytes'
                $last = $clock.ElapsedMilliseconds
            }
        }
        $file.Flush($true)
        if ($total -and $received -ne $total) {throw 'The download ended before all bytes arrived.'}
        Write-OperationProgress 'download' $Message -Completed $received -Total $total -Unit 'bytes'
    } finally {
        $request.Abort()
        if ($file) {$file.Dispose()}; if ($stream) {$stream.Dispose()}; if ($response) {$response.Dispose()}
    }
}

function Invoke-Uv {
    param([string[]]$Arguments)
    Assert-InstallNotCancelled
    $previousPreference=$ErrorActionPreference
    $output=New-Object 'Collections.Generic.List[string]'
    $helper = Get-Variable -Name GuiSource -ValueOnly -ErrorAction SilentlyContinue
    if (-not $helper) {$helper=Join-Path $PSScriptRoot "$ProductName.exe"}
    $requestPath=Join-Path $AppRoot ('updates/uv-progress-'+[guid]::NewGuid().ToString('N')+'.json')
    try {
        $ErrorActionPreference='Continue'
        if (Test-Path -LiteralPath $helper -PathType Leaf) {
            $uvVersion = (& $script:RuntimeUv --version | Out-String).Trim().Split(' ')[1]
            Write-AtomicJson $requestPath @{executable=$script:RuntimeUv;arguments=@($Arguments);root=$AppRoot;uvVersion=$uvVersion}
            & $helper --uv-progress $requestPath 2>&1 | ForEach-Object {
                $line="$_"
                if ($line.StartsWith('PDF2ZH_PROGRESS ')) {[Console]::Out.WriteLine($line)}
                else {$output.Add($line);Write-Host $line}
            }
        } else {
            Write-Host '实时进度不可用'
            & $script:RuntimeUv @Arguments 2>&1|ForEach-Object {$line="$_";$output.Add($line);Write-Host $line}
        }
        $exitCode=$LASTEXITCODE
    } finally {
        $ErrorActionPreference=$previousPreference
        Remove-Item -LiteralPath $requestPath -Force -ErrorAction SilentlyContinue
    }
    Assert-InstallNotCancelled
    if ($exitCode -ne 0) {
        $detail=$output -join [Environment]::NewLine
        if ($detail -match '(?i)failed to download|connection|network|timed out|timeout|dns|tls|certificate|502|503|504') {$script:RuntimeFailureCategory='network'}
        throw "uv failed ($exitCode): $($Arguments[0]); $detail"
    }
}

function Request-SafeMaintenance {
    param([int]$IdleSeconds = 0, [switch]$AllowLegacy)
    $listener = Get-ListeningProcessId
    if (-not $listener) { return $false }
    $health = Get-ServerHealth
    $installedVersion = (Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim()
    if (-not (Test-ExpectedHealth $health -ExpectedVersion $installedVersion) -or -not (Test-ExpectedServerProcess $listener (Get-ServerExecutable))) {
        throw 'The listening process is not this installation. Nothing was stopped.'
    }
    if (-not ($health.capabilities.PSObject.Properties.Name -contains 'updateMaintenance')) {
        throw 'Stop the legacy service after all work finishes, then perform the one-time native migration. The prepared environment is preserved.'
    }
    $token = (Get-Content -Raw -LiteralPath (Join-Path $DataDir '.control-token')).Trim()
    if ($health.maintenance.held) { return $true }
    Invoke-RestMethod -Method Post -Uri "http://${ServerHost}:$ServerPort/internal/maintenance" `
        -Headers @{'X-PDF2ZH-Control-Token'=$token} -ContentType 'application/json' `
        -Body (@{idleSeconds=$IdleSeconds}|ConvertTo-Json) -TimeoutSec 3 | Out-Null
    return $true
}

function Restore-RuntimeTransaction {
    param($Transaction)
    # Recovery is local: neither uv nor the network is used.
    $backup = Assert-ProductChild $Transaction.backup (Join-Path $AppRoot 'updates')
    $oldServer = [string]$Transaction.oldServer
    if ($Transaction.phase -in @('committed', 'rolledBack')) { return }
    Write-OperationProgress 'rollback' '正在恢复上一版本'
    Stop-ManagedControlPanel
    if (Test-Path -LiteralPath $InstalledVersionFile) {
        try { & (Join-Path $BinDir 'stop-server.ps1') -Quiet } catch { Write-RuntimeLog "Candidate stop: $_" }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $backup -File)) {
        Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $BinDir $file.Name) -Force
    }
    if ($Transaction.oldVersion) {
        Set-Content -LiteralPath $InstalledVersionFile -Value $Transaction.oldVersion -Encoding ascii
        Set-Content -LiteralPath $ExecutableFile -Value $oldServer -Encoding utf8
        if ($Transaction.wasRunning) { & (Join-Path $BinDir 'start-server.ps1') -Quiet }
    } else {
        Remove-Item -LiteralPath $InstalledVersionFile, $ExecutableFile -Force -ErrorAction SilentlyContinue
    }
    $Transaction.phase = 'rolledBack'
    Write-AtomicJson (Join-Path $AppRoot 'updates/transaction.json') $Transaction
    $statePath = Join-Path $AppRoot 'updates/state.json'
    Write-AtomicJson $statePath @{phase='failed';version=$Transaction.newVersion;error='Update interrupted or unhealthy; previous runtime restored.'}
    Write-OperationProgress 'rollback' '已恢复上一版本' -Status 'rolledBack'
}

function Apply-PreparedRuntime {
    param([switch]$KeepStopped, [switch]$AllowLegacy, [switch]$DeferLocationCommit, [switch]$NoShortcuts)
    $preparedPath = Join-Path $AppRoot 'updates/prepared.json'
    $candidate = Get-Content -Raw -Encoding utf8 -LiteralPath $preparedPath | ConvertFrom-Json
    $pendingJournal=Join-Path $AppRoot 'updates/transaction.json'
    if (Test-Path -LiteralPath $pendingJournal) {
        $pending=Get-Content -Raw -Encoding utf8 -LiteralPath $pendingJournal|ConvertFrom-Json
        if ($pending.phase -notin @('committed','rolledBack')) {throw 'An interrupted transaction must be recovered before installing.'}
    }
    $staged = Assert-ProductChild $candidate.bin (Join-Path $AppRoot 'updates')
    $environment = Assert-ProductChild $candidate.environment (Join-Path $RuntimeDir 'versions')
    $server = Join-Path $environment "Scripts/$ProductName.exe"
    if (-not (Test-Path -LiteralPath $server)) { throw 'Prepared server executable is missing.' }
    $oldServer = Get-ServerExecutable
    $oldVersion = if (Test-Path -LiteralPath $InstalledVersionFile) {(Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim()} else {''}
    Write-OperationProgress 'waiting' '等待服务空闲' -Status 'waiting'
    Assert-InstallNotCancelled
    # Finish or roll back the live-runtime transaction before returning.
    $script:InstallCommitStarted = $true
    $wasRunning = Request-SafeMaintenance -AllowLegacy:$AllowLegacy
    $transactionDir = Join-Path $AppRoot ('updates/previous/' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $transactionDir, $BinDir | Out-Null
    foreach ($file in @(Get-ChildItem -LiteralPath $BinDir -File)) { Copy-Item -LiteralPath $file.FullName -Destination $transactionDir }
    $transaction = @{phase='switching'; backup=$transactionDir; oldServer=$oldServer; oldVersion=$oldVersion;
        newVersion=$candidate.version; environment=$environment; wasRunning=($wasRunning -and -not $KeepStopped); created=(Get-Date).ToUniversalTime().ToString('o')}
    Write-AtomicJson (Join-Path $AppRoot 'updates/transaction.json') $transaction
    try {
        Write-OperationProgress 'switching' '正在切换版本'
        if ($wasRunning) { & (Join-Path $BinDir 'stop-server.ps1') -Quiet }
        Stop-ManagedControlPanel
        foreach ($file in @(Get-ChildItem -LiteralPath $staged -File)) {
            Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $BinDir $file.Name) -Force
        }
        Set-Content -LiteralPath $ExecutableFile -Value $server -Encoding utf8
        Set-Content -LiteralPath $InstalledVersionFile -Value $candidate.version -Encoding ascii
        # Validate stopped installations too, then restore their stopped state.
        Write-OperationProgress 'health' '正在检查服务'
        & (Join-Path $BinDir 'start-server.ps1') -Quiet
        if ($LASTEXITCODE -ne 0) { throw 'The candidate did not pass its health check.' }
        if ($KeepStopped -or -not $wasRunning) { & (Join-Path $BinDir 'stop-server.ps1') -Quiet }
        if (-not $DeferLocationCommit) { Save-InstallRoot -Path $AppRoot }
        if ($NoShortcuts) { Update-ProductShellIcons -Root $AppRoot -NoShortcuts } else { Write-ProductShortcuts -Root $AppRoot }
        $transaction.phase = 'committed'
        Write-AtomicJson (Join-Path $AppRoot 'updates/transaction.json') $transaction
        Write-AtomicJson (Join-Path $AppRoot 'updates/state.json') @{phase='current';lastCheck=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();version=$candidate.version}
        Remove-Item -LiteralPath $preparedPath, (Join-Path $AppRoot 'last-operation-error.txt') -Force -ErrorAction SilentlyContinue
        Write-RuntimeLog "Committed $($candidate.version); previous runtime retained for 7 days."
        Write-OperationProgress 'complete' '安装完成' -Status 'completed'
    } catch {
        $failure = $_
        Restore-RuntimeTransaction $transaction
        Set-Content -LiteralPath (Join-Path $AppRoot 'last-operation-error.txt') -Value $failure.Exception.Message -Encoding utf8
        throw $failure
    }
}

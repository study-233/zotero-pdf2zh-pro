Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PackageVersion = "1.0.0" # release-version
$ProductName = "zotero-pdf2zh-pro"
$ServerHost = "127.0.0.1"
$ServerPort = if ($env:PDF2ZH_WINDOWS_PORT) {
    [int]$env:PDF2ZH_WINDOWS_PORT
} else {
    8890
}
$HealthUrl = "http://${ServerHost}:$ServerPort/health"
$AppRoot = if ($env:PDF2ZH_WINDOWS_APP_ROOT) {
    [IO.Path]::GetFullPath($env:PDF2ZH_WINDOWS_APP_ROOT)
} else {
    Join-Path $env:LOCALAPPDATA $ProductName
}
$BinDir = Join-Path $AppRoot "bin"
$DataDir = Join-Path $AppRoot "data"
$LogsDir = Join-Path $AppRoot "logs"
$LogFile = Join-Path $LogsDir "server.log"
$PidFile = Join-Path $AppRoot "server.pid"
$ExecutableFile = Join-Path $AppRoot "server-executable.txt"
$StartMenuDir = if ($env:PDF2ZH_WINDOWS_START_MENU_DIR) {
    [IO.Path]::GetFullPath($env:PDF2ZH_WINDOWS_START_MENU_DIR)
} else {
    Join-Path ([Environment]::GetFolderPath("Programs")) $ProductName
}

function Write-Status {
    param([string]$Message, [switch]$Quiet)
    if (-not $Quiet) {
        Write-Host $Message
    }
}

function Assert-WindowsX64 {
    if ($env:OS -ne "Windows_NT") {
        throw "This package only supports Windows."
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "This package only supports 64-bit Windows."
    }
    if ([Environment]::OSVersion.Version.Major -lt 10) {
        throw "Windows 10 or Windows 11 is required."
    }
}

function Get-UvExecutable {
    $command = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $userProfile = [Environment]::GetFolderPath("UserProfile")
    $defaultUv = Join-Path $userProfile ".local\bin\uv.exe"
    if (Test-Path -LiteralPath $defaultUv) {
        return $defaultUv
    }
    return $null
}

function Get-ServerExecutable {
    if (Test-Path -LiteralPath $ExecutableFile) {
        $savedPath = (Get-Content -Raw -LiteralPath $ExecutableFile).Trim()
        if ($savedPath -and (Test-Path -LiteralPath $savedPath)) {
            return [IO.Path]::GetFullPath($savedPath)
        }
    }
    $uv = Get-UvExecutable
    if (-not $uv) {
        return $null
    }
    $toolBin = (& $uv tool dir --bin 2>$null | Select-Object -Last 1).Trim()
    if (-not $toolBin) {
        return $null
    }
    $candidate = Join-Path $toolBin "$ProductName.exe"
    if (Test-Path -LiteralPath $candidate) {
        return [IO.Path]::GetFullPath($candidate)
    }
    return $null
}

function Get-ListeningProcessId {
    $connection = Get-NetTCPConnection -State Listen -LocalPort $ServerPort -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($connection) {
        return [int]$connection.OwningProcess
    }
    return $null
}

function Get-ProcessExecutablePath {
    param([int]$ProcessId)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($process -and $process.ExecutablePath) {
        return [IO.Path]::GetFullPath($process.ExecutablePath)
    }
    return $null
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($process) {
        return [string]$process.CommandLine
    }
    return $null
}

function Test-PathEqual {
    param([string]$Left, [string]$Right)
    if (-not $Left -or -not $Right) {
        return $false
    }
    return [string]::Equals(
        [IO.Path]::GetFullPath($Left).TrimEnd("\"),
        [IO.Path]::GetFullPath($Right).TrimEnd("\"),
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Test-ExpectedServerProcess {
    param([int]$ProcessId, [string]$ServerExecutable)
    $actualExecutable = Get-ProcessExecutablePath -ProcessId $ProcessId
    if (-not (Test-PathEqual -Left $actualExecutable -Right $ServerExecutable)) {
        return $false
    }
    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if (-not $commandLine) {
        return $false
    }
    $quote = [char]34
    $expectedValues = @(
        $ServerExecutable,
        "--port $ServerPort",
        "--data-dir $quote$DataDir$quote",
        "--log-file $quote$LogFile$quote"
    )
    foreach ($expected in $expectedValues) {
        if ($commandLine.IndexOf($expected, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            return $false
        }
    }
    return $true
}

function Get-ServerHealth {
    $response = $null
    $reader = $null
    try {
        $request = [Net.WebRequest]::Create($HealthUrl)
        $request.Proxy = $null
        $request.Timeout = 2000
        $response = $request.GetResponse()
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        return ($reader.ReadToEnd() | ConvertFrom-Json)
    } catch {
        return $null
    } finally {
        if ($reader) {
            $reader.Dispose()
        }
        if ($response) {
            $response.Dispose()
        }
    }
}

function Test-ExpectedHealth {
    param($Health)
    if (-not $Health -or $Health.version -ne $PackageVersion) {
        return $false
    }
    if (-not $Health.workspace -or -not $Health.workspace.writable) {
        return $false
    }
    return Test-PathEqual -Left $Health.workspace.path -Right $DataDir
}

function Save-ManagedProcess {
    param([int]$ProcessId, [string]$ServerExecutable)
    New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null
    Set-Content -LiteralPath $PidFile -Value $ProcessId -Encoding ascii
    Set-Content -LiteralPath $ExecutableFile -Value $ServerExecutable -Encoding utf8
}

function Remove-ManagedProcessState {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

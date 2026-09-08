Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PackageVersion = "1.6.5" # release-version
$ProductName = "zotero-pdf2zh-pro"
$ServerHost = "127.0.0.1"
$ServerPort = if ($env:PDF2ZH_WINDOWS_PORT) {
    [int]$env:PDF2ZH_WINDOWS_PORT
} else {
    8890
}
$HealthUrl = "http://${ServerHost}:$ServerPort/health"
$InstallRegistryKey = if ($env:PDF2ZH_WINDOWS_REGISTRY_KEY) {
    $env:PDF2ZH_WINDOWS_REGISTRY_KEY
} else {
    "HKCU:\Software\$ProductName"
}
$DefaultAppRoot = Join-Path $env:LOCALAPPDATA $ProductName

function Get-InstallRegistrySubKeyPath {
    $prefix = "HKCU:\"
    if (-not $InstallRegistryKey.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The installation registry key must be under HKCU: $InstallRegistryKey"
    }
    $subKeyPath = $InstallRegistryKey.Substring($prefix.Length)
    if ([string]::IsNullOrWhiteSpace($subKeyPath)) {
        throw "The installation registry key must name an HKCU subkey."
    }
    return $subKeyPath
}

function Get-SavedInstallRoot {
    $key = $null
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey((Get-InstallRegistrySubKeyPath), $false)
        if (-not $key) {
            return $null
        }
        $saved = $key.GetValue(
            "InstallRoot",
            $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
        if ($saved) {
            return [IO.Path]::GetFullPath([string]$saved)
        }
    } catch {
        return $null
    } finally {
        if ($key) {
            $key.Dispose()
        }
    }
    return $null
}

$savedInstallRoot = Get-SavedInstallRoot
$AppRoot = if ($env:PDF2ZH_WINDOWS_APP_ROOT) {
    [IO.Path]::GetFullPath($env:PDF2ZH_WINDOWS_APP_ROOT)
} elseif ($savedInstallRoot) {
    $savedInstallRoot
} else {
    $DefaultAppRoot
}
$BinDir = Join-Path $AppRoot "bin"
$DataDir = Join-Path $AppRoot "data"
$LogsDir = Join-Path $AppRoot "logs"
$RuntimeDir = Join-Path $AppRoot "runtime"
$UvInstallDir = Join-Path $RuntimeDir "uv"
$UvToolsDir = Join-Path $RuntimeDir "tools"
$UvToolBinDir = Join-Path $RuntimeDir "tool-bin"
$UvPythonDir = Join-Path $RuntimeDir "python"
$UvCacheDir = Join-Path $AppRoot "cache"
$PrivateUvExecutable = Join-Path $UvInstallDir "uv.exe"
$LogFile = Join-Path $LogsDir "server.log"
$ControlLogFile = Join-Path $LogsDir "control-panel.log"
$PidFile = Join-Path $AppRoot "server.pid"
$ExecutableFile = Join-Path $AppRoot "server-executable.txt"
$InstalledVersionFile = Join-Path $AppRoot "installed-version.txt"
$ControlPanelExecutable = Join-Path $BinDir "$ProductName.exe"
$ControlPanelPidFile = Join-Path $AppRoot "control-panel.pid"
$ControlPanelExecutableFile = Join-Path $AppRoot "control-panel-executable.txt"
$AutostartRunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$AutostartApprovedKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
$StartMenuDir = if ($env:PDF2ZH_WINDOWS_START_MENU_DIR) {
    [IO.Path]::GetFullPath($env:PDF2ZH_WINDOWS_START_MENU_DIR)
} else {
    Join-Path ([Environment]::GetFolderPath("Programs")) $ProductName
}

function Save-InstallRoot {
    param([string]$Path = $AppRoot)
    $resolved = [IO.Path]::GetFullPath($Path)
    $key = $null
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey((Get-InstallRegistrySubKeyPath), $true)
        if (-not $key) {
            throw "The installation registry key could not be opened for writing: $InstallRegistryKey"
        }
        $key.SetValue("InstallRoot", $resolved, [Microsoft.Win32.RegistryValueKind]::String)
    } finally {
        if ($key) {
            $key.Dispose()
        }
    }
}

function Remove-InstallRoot {
    $subKeyPath = Get-InstallRegistrySubKeyPath
    $key = $null
    $removeEmptyKey = $false
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($subKeyPath, $true)
        if (-not $key) {
            return
        }
        $key.DeleteValue("InstallRoot", $false)
        $removeEmptyKey = $key.ValueCount -eq 0 -and $key.SubKeyCount -eq 0
    } finally {
        if ($key) {
            $key.Dispose()
        }
    }
    if ($removeEmptyKey) {
        [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey($subKeyPath, $false)
    }
}

function Get-WindowsNativeErrorCode {
    param([Exception]$Exception)
    $current = $Exception
    while ($current) {
        if ($current -is [ComponentModel.Win32Exception]) {
            return $current.NativeErrorCode
        }
        if ($current.HResult -eq -2147024774) {
            return 122
        }
        $current = $current.InnerException
    }
    return $null
}

function Invoke-WindowsInteropOperation {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [scriptblock]$OnRetry,
        [int[]]$RetryDelaysMilliseconds = @(200, 500)
    )
    for ($attempt = 1; $attempt -le ($RetryDelaysMilliseconds.Count + 1); $attempt++) {
        try {
            return (& $Action)
        } catch {
            $errorRecord = $_
            $errorCode = Get-WindowsNativeErrorCode -Exception $errorRecord.Exception
            if ($errorCode -ne 122 -or $attempt -gt $RetryDelaysMilliseconds.Count) {
                throw
            }
            $delay = $RetryDelaysMilliseconds[$attempt - 1]
            if ($OnRetry) {
                & $OnRetry $attempt ($attempt + 1) $delay $errorRecord
            }
            Start-Sleep -Milliseconds $delay
        }
    }
}

function Use-PrivateUvEnvironment {
    $env:UV_INSTALL_DIR = $UvInstallDir
    $env:UV_TOOL_DIR = $UvToolsDir
    $env:UV_TOOL_BIN_DIR = $UvToolBinDir
    $env:UV_PYTHON_INSTALL_DIR = $UvPythonDir
    $env:UV_CACHE_DIR = $UvCacheDir
}

if (Test-Path -LiteralPath $PrivateUvExecutable -PathType Leaf) {
    Use-PrivateUvEnvironment
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
    if (Test-Path -LiteralPath $PrivateUvExecutable -PathType Leaf) {
        Use-PrivateUvEnvironment
        return $PrivateUvExecutable
    }
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
    $connection = Get-NetTCPConnection -State Listen -LocalPort $ServerPort -ErrorAction SilentlyContinue |
        Select-Object -First 1
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

function Get-ToolPythonExecutable {
    $uv = Get-UvExecutable
    if (-not $uv) {
        return $null
    }
    $toolRoot = (& $uv tool dir 2>$null | Select-Object -Last 1).Trim()
    if (-not $toolRoot) {
        return $null
    }
    $candidate = Join-Path (Join-Path $toolRoot $ProductName) "Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        return [IO.Path]::GetFullPath($candidate)
    }
    return $null
}

function Test-ExpectedServerProcess {
    param([int]$ProcessId, [string]$ServerExecutable)
    $actualExecutable = Get-ProcessExecutablePath -ProcessId $ProcessId
    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if (-not $actualExecutable -or -not $commandLine) {
        return $false
    }
    if (-not (Test-PathEqual -Left $actualExecutable -Right $ServerExecutable)) {
        $toolPython = Get-ToolPythonExecutable
        if (
            -not $toolPython -or
            -not [string]::Equals(
                [IO.Path]::GetFileName($actualExecutable),
                "python.exe",
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            $commandLine.IndexOf($toolPython, [StringComparison]::OrdinalIgnoreCase) -lt 0
        ) {
            return $false
        }
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

function Get-ManagedControlPanelProcessId {
    if (
        -not (Test-Path -LiteralPath $ControlPanelPidFile) -or
        -not (Test-Path -LiteralPath $ControlPanelExecutableFile)
    ) {
        return $null
    }
    $rawProcessId = (Get-Content -Raw -LiteralPath $ControlPanelPidFile).Trim()
    $savedExecutable = (Get-Content -Raw -LiteralPath $ControlPanelExecutableFile).Trim()
    if ($rawProcessId -notmatch "^\d+$") {
        return $null
    }
    if (-not (Test-PathEqual -Left $savedExecutable -Right $ControlPanelExecutable)) {
        return $null
    }
    $controlProcessId = [int]$rawProcessId
    $actualExecutable = Get-ProcessExecutablePath -ProcessId $controlProcessId
    if (-not (Test-PathEqual -Left $actualExecutable -Right $ControlPanelExecutable)) {
        return $null
    }
    return $controlProcessId
}

function Remove-ManagedControlPanelState {
    Remove-Item -LiteralPath $ControlPanelPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ControlPanelExecutableFile -Force -ErrorAction SilentlyContinue
}

function Stop-ManagedControlPanel {
    $controlProcessId = Get-ManagedControlPanelProcessId
    if (-not $controlProcessId) {
        Remove-ManagedControlPanelState
        return
    }
    Stop-Process -Id $controlProcessId
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (-not (Get-Process -Id $controlProcessId -ErrorAction SilentlyContinue)) {
            Remove-ManagedControlPanelState
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "The managed control center did not stop within 10 seconds."
}

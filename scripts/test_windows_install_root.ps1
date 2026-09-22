Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$windowsDir = Join-Path $PSScriptRoot "windows"
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("pdf2zh-install-root-test-" + [guid]::NewGuid().ToString("N"))
$testRegistrySubKey = "Software\pdf2zh-root-test-" + [guid]::NewGuid().ToString("N")
$savedEnvironment = @{}
foreach ($name in @("PDF2ZH_WINDOWS_APP_ROOT", "PDF2ZH_WINDOWS_REGISTRY_KEY", "TEMP", "TMP")) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    if ($Actual -ne $Expected) { throw "${Message}: expected '$Expected', got '$Actual'." }
}

function New-TestInstallation {
    param([string]$Root)
    $bin = Join-Path $Root "bin"
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $bin "zotero-pdf2zh-pro.exe") -Value "fixture"
    Copy-Item -LiteralPath (Join-Path $windowsDir "common.ps1") -Destination (Join-Path $bin "common.ps1")
    Set-Content -LiteralPath (Join-Path $Root "installed-version.txt") -Value "invalid-version"
}

function Assert-Resolution {
    param([string]$ExplicitRoot, [string]$SavedRoot, [string]$ScriptDirectory, [string]$ExpectedRoot, [string]$ExpectedSource)
    $result = Resolve-InstallRoot -ExplicitRoot $ExplicitRoot -SavedRoot $SavedRoot `
        -ScriptDirectory $ScriptDirectory -DefaultRoot $defaultRoot
    Assert-Equal $result.Root $ExpectedRoot "Installation root"
    Assert-Equal $result.Source $ExpectedSource "Installation root source"
}

function Test-UpdateRoot {
    param([string]$Name, [string]$InheritedRoot, [bool]$PassInstallRoot, [bool]$ExpectedDefer, [switch]$FailInstall, [switch]$UseRegistry)
    $caseRoot = Join-Path $testRoot $Name
    $package = Join-Path $caseRoot "package"
    $target = Join-Path $caseRoot "安装 target"
    New-Item -ItemType Directory -Path $package, (Join-Path $target "bin") -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $target "bin\zotero-pdf2zh-pro.exe") -Value "fixture"
    if ($UseRegistry) {
        New-TestInstallation $target
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($testRegistrySubKey)
        try { $key.SetValue("InstallRoot", $target) } finally { $key.Dispose() }
    }
    Copy-Item -LiteralPath (Join-Path $windowsDir "apply-update.ps1"), (Join-Path $windowsDir "common.ps1") -Destination $package
    @'

function Start-Process {
    param([string]$FilePath, $ArgumentList, [string]$WindowStyle)
    Add-Content -LiteralPath (Join-Path $AppRoot "launches.txt") -Value $FilePath
    if ($FilePath -eq $ControlPanelExecutable) {
        @{ RootOverride = $env:PDF2ZH_WINDOWS_APP_ROOT } | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $AppRoot "relaunch-environment.json") -Encoding utf8
    }
}
'@ | Add-Content -LiteralPath (Join-Path $package "common.ps1") -Encoding utf8
    @'
param([switch]$Quiet)
. (Join-Path $PSScriptRoot "common.ps1")
Set-Content -LiteralPath (Join-Path $AppRoot "stop-root.txt") -Value $AppRoot
'@ | Set-Content -LiteralPath (Join-Path $package "stop-server.ps1") -Encoding utf8
    @'
param([string]$GuiSource, [string]$InstallRoot, [switch]$NonInteractive, [switch]$DeferLocationCommit, [string]$PackageSource)
@{ Root = $InstallRoot; Defer = [bool]$DeferLocationCommit } | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $InstallRoot "install-arguments.json") -Encoding utf8
'@ | Set-Content -LiteralPath (Join-Path $package "install.ps1") -Encoding utf8
    if ($FailInstall) {
        Add-Content -LiteralPath (Join-Path $package "install.ps1") -Value 'throw "Expected fixture installation failure"'
    }
    $env:PDF2ZH_WINDOWS_APP_ROOT = if ($InheritedRoot -eq "target") { $target } else { $InheritedRoot }
    $env:TEMP = $caseRoot
    $env:TMP = $caseRoot
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $package "apply-update.ps1"), "-ParentProcessId", "2147483647")
    if ($PassInstallRoot) { $arguments += @("-InstallRoot", $target) }
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @arguments | Out-Null
    $expectedExitCode = if ($FailInstall) { 1 } else { 0 }
    Assert-Equal $LASTEXITCODE $expectedExitCode "Update fixture exit code"
    $captured = Get-Content -Raw -LiteralPath (Join-Path $target "install-arguments.json") | ConvertFrom-Json
    Assert-Equal $captured.Root $target "Update installation destination"
    Assert-Equal $captured.Defer $ExpectedDefer "Update registry commit policy"
    Assert-Equal ((Get-Content -Raw -LiteralPath (Join-Path $target "stop-root.txt")).Trim()) $target "Update stop-server destination"
    Assert-Equal ((Get-Content -LiteralPath (Join-Path $target "launches.txt"))[0]) (Join-Path $target "bin\zotero-pdf2zh-pro.exe") "Updated GUI destination"
    $relaunchEnvironment = Get-Content -Raw -LiteralPath (Join-Path $target "relaunch-environment.json") | ConvertFrom-Json
    $expectedOverride = if ($ExpectedDefer) { $target } else { $null }
    Assert-Equal $relaunchEnvironment.RootOverride $expectedOverride "Relaunched GUI installation override"
}

try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $env:PDF2ZH_WINDOWS_APP_ROOT = $null
    $env:PDF2ZH_WINDOWS_REGISTRY_KEY = "HKCU:\$testRegistrySubKey"
    . (Join-Path $windowsDir "common.ps1")
    Assert-Equal $InstallRegistryReadStatus "missing" "Missing registry diagnostics"
    Assert-Equal $InstallRegistryReadErrorCode $null "Missing registry has no error code"

    $localRoot = Join-Path $testRoot "中文 installation"
    $registeredRoot = Join-Path $testRoot "registered"
    $defaultRoot = Join-Path $testRoot "default"
    $staleRoot = Join-Path $testRoot "retained custom data"
    New-TestInstallation $localRoot
    New-TestInstallation $registeredRoot
    $localBin = Join-Path $localRoot "bin"
    Assert-Resolution (Join-Path $testRoot "explicit") $registeredRoot $localBin (Join-Path $testRoot "explicit") "environment"
    Assert-Resolution "" $registeredRoot $localBin $registeredRoot "registry"
    Assert-Resolution "" "" $localBin $localRoot "script"
    Assert-Resolution "" $staleRoot $localBin $localRoot "script"
    Assert-Resolution "" $staleRoot $windowsDir $staleRoot "registry-reinstall"
    Assert-Resolution "" "" $windowsDir $defaultRoot "default"
    foreach ($relativeRoot in @("relative-root", "D:relative-root", "\relative-root")) {
        Assert-Resolution "" $relativeRoot $windowsDir $defaultRoot "default"
    }

    Set-Content -LiteralPath (Join-Path $localRoot "installed-version.txt") -Value ""
    Assert-Resolution "" "" $localBin $localRoot "script"
    $discovered = & { . (Join-Path $localBin "common.ps1"); @{ Root = $AppRoot; Source = $InstallRootSource } }
    Assert-Equal $discovered.Root $localRoot "Installed script discovers itself"
    Assert-Equal $discovered.Source "script" "Installed script discovery source"

    foreach ($relative in @("bin\zotero-pdf2zh-pro.exe", "bin\common.ps1", "installed-version.txt")) {
        $path = Join-Path $localRoot $relative
        $backup = [IO.File]::ReadAllBytes($path)
        Remove-Item -LiteralPath $path
        Assert-Resolution "" "" $localBin $defaultRoot "default"
        New-Item -ItemType Directory -Path $path | Out-Null
        Assert-Resolution "" "" $localBin $defaultRoot "default"
        Remove-Item -LiteralPath $path
        [IO.File]::WriteAllBytes($path, $backup)
    }
    foreach ($folder in @("zip", "updates\candidate\package", "logs")) {
        $candidate = Join-Path $testRoot $folder
        New-Item -ItemType Directory -Path $candidate -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $localBin "common.ps1") -Destination (Join-Path $candidate "common.ps1")
        Assert-Resolution "" "" $candidate $defaultRoot "default"
    }

    $InstallRegistryKey = "HKLM:\invalid-for-this-reader"
    $null = Get-SavedInstallRoot
    Assert-Equal $InstallRegistryReadStatus "error" "Registry failures remain diagnosable"
    if ($null -eq $InstallRegistryReadErrorCode) { throw "Registry read failure lost its error code." }

    Test-UpdateRoot -Name "normal-update" -InheritedRoot "" -PassInstallRoot $true -ExpectedDefer $false
    Test-UpdateRoot -Name "explicit-override-update" -InheritedRoot $staleRoot -PassInstallRoot $true -ExpectedDefer $true
    Test-UpdateRoot -Name "legacy-update-invocation" -InheritedRoot "target" -PassInstallRoot $false -ExpectedDefer $true
    Test-UpdateRoot -Name "legacy-registered-update" -InheritedRoot "" -PassInstallRoot $false -ExpectedDefer $false -UseRegistry
    Test-UpdateRoot -Name "failed-normal-update" -InheritedRoot "" -PassInstallRoot $true -ExpectedDefer $false -FailInstall
    Test-UpdateRoot -Name "failed-explicit-override-update" -InheritedRoot $staleRoot -PassInstallRoot $true -ExpectedDefer $true -FailInstall
    Write-Host "Windows installation root discovery and update handoff tests passed."
} finally {
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($testRegistrySubKey, $false)
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
    }
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $expectedParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ((Split-Path $resolvedTestRoot -Parent).TrimEnd('\') -ne $expectedParent -or
        (Split-Path $resolvedTestRoot -Leaf) -notlike "pdf2zh-install-root-test-*") {
        throw "Refusing to remove an unexpected test directory: $resolvedTestRoot"
    }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force -ErrorAction SilentlyContinue
}

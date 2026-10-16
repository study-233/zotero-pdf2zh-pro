param(
    [string]$LlvmRoot = $env:LLVM_HOME,
    [string]$SkiaSourceRoot = $env:SKIA_SOURCE_DIR,
    [string]$TargetDirectory = $env:CARGO_TARGET_DIR,
    [string]$ManifestPath = 'windows-app/Cargo.toml',
    [switch]$RunTests,
    [string]$TestFilter
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'workspace.ps1')
$probeRepository = Split-Path $PSScriptRoot -Parent
$physicalRepository = $probeRepository
function Convert-BuildPath([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = $physicalRepository.TrimEnd('\') + '\'
    if ($env:PDF2ZH_WINDOWS_BUILD_ROOT -and $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        return Join-Path $env:PDF2ZH_WINDOWS_BUILD_ROOT $resolved.Substring($prefix.Length)
    }
    return $resolved
}
if ($env:PDF2ZH_WINDOWS_BUILD_ROOT) { $probeRepository = $env:PDF2ZH_WINDOWS_BUILD_ROOT }
$probeLlvm = Convert-BuildPath (Resolve-Path -LiteralPath $LlvmRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $probeLlvm 'bin/clang-cl.exe'))) { throw 'LLVM clang-cl is missing' }
$probeVsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$probeVs = & $probeVsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $probeVs) { throw 'Visual Studio C++ tools are missing' }
$probeVcVars = Join-Path $probeVs 'VC/Auxiliary/Build/vcvars64.bat'
if (-not (Test-Path -LiteralPath $probeVcVars)) { throw 'Visual Studio C++ environment is missing' }
$probeEnvironment = & cmd.exe /d /s /c "call `"$probeVcVars`" >nul && set"
if ($LASTEXITCODE -ne 0) { throw 'Visual Studio environment initialization failed' }
foreach ($probeLine in $probeEnvironment) {
    if ($probeLine -match '^(PATH|INCLUDE|LIB|LIBPATH|VC[^=]*|VS[^=]*|WindowsSDK[^=]*|UCRT[^=]*|UniversalCRT[^=]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
$env:LLVM_HOME = $probeLlvm
$env:LIBCLANG_PATH = Join-Path $probeLlvm 'bin'
$env:CLANG_PATH = Join-Path $probeLlvm 'bin/clang.exe'
$env:Path = (Join-Path $probeLlvm 'bin') + ';' + $env:Path
$clangResources = Join-Path $probeLlvm 'lib/clang/21'
if (-not (Test-Path -LiteralPath (Join-Path $clangResources 'include/stddef.h'))) { throw 'LLVM 21 resource headers are missing' }
$env:BINDGEN_EXTRA_CLANG_ARGS = ($env:BINDGEN_EXTRA_CLANG_ARGS + ' -resource-dir="' + $clangResources.Replace('\', '/') + '"').Trim()
if (-not $TargetDirectory) { $TargetDirectory = Join-Path $probeRepository 'windows-app/target' }
$env:CARGO_TARGET_DIR = Convert-BuildPath $TargetDirectory
$env:RUSTFLAGS = '-C target-feature=+crt-static'
$env:FORCE_SKIA_BUILD = '1'
if ($SkiaSourceRoot) {
    $env:SKIA_SOURCE_DIR = Convert-BuildPath (Resolve-Path -LiteralPath $SkiaSourceRoot).Path
    $probeGn = Join-Path $env:SKIA_SOURCE_DIR 'bin/gn.exe'
    if (Test-Path -LiteralPath $probeGn) { $env:SKIA_GN_COMMAND = $probeGn }
}
$probeNinja = Join-Path $probeVs 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe'
if (Test-Path -LiteralPath $probeNinja) { $env:SKIA_NINJA_COMMAND = $probeNinja }
$env:CARGO_BUILD_JOBS = '4'
$probeLogs = New-WorkspaceReport -Name 'windows-build'
New-Item -ItemType Directory -Force -Path $probeLogs | Out-Null
$probeCommand = if ($RunTests) { 'test' } else { 'build' }
$cargoArguments = @($probeCommand, '--release', '--locked', '--target', 'x86_64-pc-windows-msvc', '--manifest-path', (Join-Path $probeRepository $ManifestPath))
if ($RunTests -and $TestFilter) { $cargoArguments += $TestFilter }
$previousErrorPreference = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 wraps native stderr as ErrorRecord even for normal
    # Cargo progress. Judge success by the process exit code, not its log stream.
    $ErrorActionPreference = 'Continue'
    & cargo +stable-x86_64-pc-windows-msvc @cargoArguments 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath (Join-Path $probeLogs "$probeCommand.log")
    $buildExitCode = $LASTEXITCODE
} finally { $ErrorActionPreference = $previousErrorPreference }
if ($buildExitCode -ne 0) { exit $buildExitCode }
if (-not $RunTests -and $ManifestPath -eq 'windows-app/Cargo.toml') {
    & python (Join-Path $PSScriptRoot 'generate_windows_notices.py') --skia-source $env:SKIA_SOURCE_DIR --output (Join-Path $env:CARGO_TARGET_DIR 'THIRD_PARTY_NOTICES.md')
}
exit $LASTEXITCODE

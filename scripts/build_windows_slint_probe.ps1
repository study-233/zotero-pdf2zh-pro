param([Parameter(Mandatory = $true)][string]$LlvmRoot, [string]$SkiaSourceRoot)
$ErrorActionPreference = 'Stop'
$probeRepository = Split-Path $PSScriptRoot -Parent
$probeLlvm = (Resolve-Path -LiteralPath $LlvmRoot).Path
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
$env:CARGO_TARGET_DIR = Join-Path $probeRepository '.tmp/slint-target'
$env:RUSTFLAGS = '-C target-feature=+crt-static'
$env:FORCE_SKIA_BUILD = '1'
if ($SkiaSourceRoot) {
    $env:SKIA_SOURCE_DIR = (Resolve-Path -LiteralPath $SkiaSourceRoot).Path
    $probeGn = Join-Path $env:SKIA_SOURCE_DIR 'bin/gn.exe'
    if (Test-Path -LiteralPath $probeGn) { $env:SKIA_GN_COMMAND = $probeGn }
}
$probeNinja = Join-Path $probeVs 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe'
if (Test-Path -LiteralPath $probeNinja) { $env:SKIA_NINJA_COMMAND = $probeNinja }
$env:CARGO_BUILD_JOBS = '4'
$probeLogs = Join-Path $probeRepository '.tmp/slint-gate'
New-Item -ItemType Directory -Force -Path $probeLogs | Out-Null
& cargo +stable-x86_64-pc-windows-msvc build --release --locked --target x86_64-pc-windows-msvc --manifest-path (Join-Path $probeRepository 'windows-app/slint-probe/Cargo.toml') 2>&1 | Tee-Object -FilePath (Join-Path $probeLogs 'build.log')
exit $LASTEXITCODE

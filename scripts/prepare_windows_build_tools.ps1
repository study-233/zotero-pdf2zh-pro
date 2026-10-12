param([string]$CacheRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) '.tmp/windows-build-tools'))
$ErrorActionPreference = 'Stop'
$CacheRoot = [IO.Path]::GetFullPath($CacheRoot)
New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
function Get-VerifiedArchive([string]$Url, [string]$Path, [string]$Sha256) {
    if ((Test-Path -LiteralPath $Path) -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ieq $Sha256) { return }
    $temporary = "$Path.partial"
    Invoke-WebRequest -Uri $Url -OutFile $temporary
    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ine $Sha256) { throw "Checksum mismatch: $Url" }
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
$llvmArchive = Join-Path $CacheRoot 'LLVM-21.1.8-win64.exe'
Get-VerifiedArchive 'https://github.com/llvm/llvm-project/releases/download/llvmorg-21.1.8/LLVM-21.1.8-win64.exe' $llvmArchive '7a5386c26497db1691f320121e5b113364dd0274b98e55f15f4dbc00c0450113'
# Never execute an expanded tree restored from the shared archive cache.
$toolsRoot = Join-Path ([IO.Path]::GetTempPath()) ("pdf2zh-renderer-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $toolsRoot | Out-Null
$llvmRoot = Join-Path $toolsRoot 'llvm'
# Extract the portable files; do not run the installer or change system PATH.
& 7z x -y $llvmArchive "-o$llvmRoot" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'LLVM extraction failed' }
$skiaArchive = Join-Path $CacheRoot 'skia-m153-0.101.2.tar.gz'
Get-VerifiedArchive 'https://codeload.github.com/rust-skia/skia/tar.gz/m153-0.101.2' $skiaArchive '30c8b51a0a32fae63562c303bdc91605519a9598951422610e3fb8685b3c4f6f'
$skiaParent = Join-Path $toolsRoot 'skia-source'
$skiaRoot = Join-Path $skiaParent 'skia-m153-0.101.2'
& python (Join-Path $PSScriptRoot 'prepare_windows_skia_source.py') --archive $skiaArchive --destination $skiaParent
if ($LASTEXITCODE -ne 0) { throw 'Skia extraction failed' }
& python (Join-Path $PSScriptRoot 'prepare_windows_skia_dependencies.py') $skiaRoot
if ($LASTEXITCODE -ne 0) { throw 'Skia dependency preparation failed' }
if ($env:GITHUB_ENV) {
    "LLVM_HOME=$llvmRoot" >> $env:GITHUB_ENV
    "SKIA_SOURCE_DIR=$skiaRoot" >> $env:GITHUB_ENV
}
$env:LLVM_HOME=$llvmRoot
$env:SKIA_SOURCE_DIR=$skiaRoot
Write-Host "Prepared LLVM and Skia in $toolsRoot"

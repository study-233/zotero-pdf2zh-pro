param([string]$UvExecutable)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not $UvExecutable) {$UvExecutable=(Get-Command uv.exe -ErrorAction Stop).Source}
$directory=Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-cleanup-test-'+[guid]::NewGuid().ToString('N'))
$previous=$env:PDF2ZH_WINDOWS_APP_ROOT
$previousRegistry=$env:PDF2ZH_WINDOWS_REGISTRY_KEY
$env:PDF2ZH_WINDOWS_APP_ROOT=$directory
$env:PDF2ZH_WINDOWS_REGISTRY_KEY='HKCU:\Software\pdf2zh-cleanup-isolated-test'
try {
    $legacy=Join-Path $directory 'runtime/tools/zotero-pdf2zh-pro'
    $active=Join-Path $directory 'runtime/versions/current'
    $other=Join-Path $directory 'runtime/tools/unrelated-tool'
    foreach ($path in @($legacy,$active,$other)) {
        New-Item -ItemType Directory -Force -Path (Join-Path $path 'Scripts')|Out-Null
        Set-Content -LiteralPath (Join-Path $path 'Scripts/zotero-pdf2zh-pro.exe') -Value 'fixture'
        (Get-Item -LiteralPath $path).LastWriteTimeUtc=(Get-Date).ToUniversalTime().AddDays(-8)
    }
    $privateUv=Join-Path $directory 'runtime/uv/uv.exe'
    New-Item -ItemType Directory -Force -Path (Split-Path $privateUv -Parent), (Join-Path $directory 'runtime/tool-bin'), (Join-Path $directory 'updates'), (Join-Path $directory 'data')|Out-Null
    Copy-Item -LiteralPath $UvExecutable -Destination $privateUv
    $launcher=Join-Path $directory 'runtime/tool-bin/zotero-pdf2zh-pro.exe'
    Set-Content -LiteralPath $launcher -Value 'legacy launcher'
    Set-Content -LiteralPath (Join-Path $directory 'server-executable.txt') -Value (Join-Path $active 'Scripts/zotero-pdf2zh-pro.exe') -Encoding utf8
    Set-Content -LiteralPath (Join-Path $directory 'data/preserved.txt') -Value 'data'
    $journal=Join-Path $directory 'updates/transaction.json'
    $transaction=@{phase='committed';oldServer=(Join-Path $legacy 'Scripts/zotero-pdf2zh-pro.exe');created=(Get-Date).ToUniversalTime().ToString('o')}
    $transaction|ConvertTo-Json|Set-Content -LiteralPath $journal -Encoding utf8
    $cleanup=Join-Path $PSScriptRoot 'windows/clean-cache.ps1'
    & "$PSHOME/powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $cleanup
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $legacy)) {throw 'Recent rollback runtime was removed.'}
    $cleanupLog = Join-Path $directory 'logs/control-panel.log'
    if ((Get-Content -Raw -LiteralPath $cleanupLog) -notmatch 'Space report logicalBytes=') {throw 'Manual cleanup did not report disk usage.'}
    $transaction.created=(Get-Date).ToUniversalTime().AddDays(-8).ToString('o')
    $transaction|ConvertTo-Json|Set-Content -LiteralPath $journal -Encoding utf8
    $heldData = [IO.File]::Open((Join-Path $directory 'data/preserved.txt'), 'Open', 'ReadWrite', 'None')
    try {
        # Full space reporting would attempt to open this live file and fail.
        & "$PSHOME/powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $cleanup -SkipSpaceReport
        if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $legacy) -or (Test-Path -LiteralPath $launcher)) {throw 'Automatic cleanup failed or did not reclaim the expired environment.'}
    } finally { $heldData.Dispose() }
    if (@(Select-String -LiteralPath $cleanupLog -SimpleMatch 'Space report logicalBytes=').Count -ne 1) {throw 'Automatic cleanup scanned every file for a space report.'}
    foreach ($path in @($active,$other,(Join-Path $directory 'data/preserved.txt'))) {
        if (-not (Test-Path -LiteralPath $path)) {throw "Cleanup removed protected content: $path"}
    }
    Write-Host 'Seven-day legacy cleanup preserves rollback, active environment, unrelated tools and user data.'
} finally {
    $env:PDF2ZH_WINDOWS_APP_ROOT=$previous
    $env:PDF2ZH_WINDOWS_REGISTRY_KEY=$previousRegistry
    $resolved=[IO.Path]::GetFullPath($directory)
    if ((Split-Path $resolved -Parent) -ne [IO.Path]::GetTempPath().TrimEnd('\') -or (Split-Path $resolved -Leaf) -notlike 'pdf2zh-cleanup-test-*') {throw 'Unsafe test cleanup path.'}
    Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
}

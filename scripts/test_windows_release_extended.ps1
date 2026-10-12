param(
    [Parameter(Mandatory=$true)][string]$Version,
    [switch]$SkipLifecycle,
    [switch]$SkipPreviousRelease,
    [string]$DownloadCache = $env:PDF2ZH_TEST_DOWNLOAD_CACHE
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$fixture = Join-Path $repo ('.tmp/release-extended-' + [guid]::NewGuid().ToString('N'))
$registry = 'HKCU:\Software\pdf2zh-release-test-' + [guid]::NewGuid().ToString('N')
$package = (Resolve-Path (Join-Path $repo 'dist/zotero-pdf2zh-pro-windows-x64.zip')).Path
$wheel = (Resolve-Path (Join-Path $repo "server/dist/zotero_pdf2zh_pro-$Version-py3-none-any.whl")).Path
$saved = @{}
foreach ($name in @('TEMP','TMP','UV_TOOL_DIR','UV_TOOL_BIN_DIR','PDF2ZH_DATA_DIR',
    'PDF2ZH_WINDOWS_APP_ROOT','PDF2ZH_WINDOWS_PORT','PDF2ZH_WINDOWS_REGISTRY_KEY',
    'PDF2ZH_WINDOWS_START_MENU_DIR','PDF2ZH_WINDOWS_LIFECYCLE_PREPARED_CACHE')) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'temp') | Out-Null
$env:TEMP = Join-Path $fixture 'temp'
$env:TMP = $env:TEMP
$env:UV_TOOL_DIR = Join-Path $fixture 'uv-tools'
$env:UV_TOOL_BIN_DIR = Join-Path $fixture 'uv-bin'
$env:PDF2ZH_DATA_DIR = Join-Path $fixture 'server-data'
$env:PDF2ZH_WINDOWS_APP_ROOT = Join-Path $fixture 'installation 中文 with spaces'
$env:PDF2ZH_WINDOWS_PORT = '18890'
$env:PDF2ZH_WINDOWS_REGISTRY_KEY = $registry
$env:PDF2ZH_WINDOWS_START_MENU_DIR = Join-Path $fixture 'menu'
$env:PDF2ZH_WINDOWS_LIFECYCLE_PREPARED_CACHE = $null
try {
    if (-not $SkipLifecycle) {
        if ($DownloadCache) {
            $cache = (Resolve-Path -LiteralPath $DownloadCache).Path
            if (-not $cache.StartsWith((Join-Path $repo '.tmp')+'\', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Only an isolated repository download cache may be reused.'
            }
            New-Item -ItemType Directory -Force -Path (Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT 'runtime') | Out-Null
            Copy-Item -LiteralPath (Join-Path $cache 'cache') -Destination (Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT 'cache') -Recurse
            Copy-Item -LiteralPath (Join-Path $cache 'runtime/python') -Destination (Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT 'runtime/python') -Recurse
            $env:PDF2ZH_WINDOWS_LIFECYCLE_PREPARED_CACHE = '1'
        }
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'test_windows_lifecycle.ps1') -WindowsPackage $package -PackageSource $wheel
        if ($LASTEXITCODE -ne 0) { throw 'Extended lifecycle validation failed.' }
    }
    if (-not $SkipPreviousRelease) {
        $previous = Join-Path $fixture 'previous-release'
        New-Item -ItemType Directory -Force -Path $previous | Out-Null
        gh release download v1.8.2 --repo study-233/zotero-pdf2zh-pro --pattern 'zotero-pdf2zh-pro-windows-x64.zip' --dir $previous
        if ($LASTEXITCODE -ne 0) { throw 'Previous Windows package download failed.' }
        $metadata = Invoke-RestMethod 'https://pypi.org/pypi/zotero-pdf2zh-pro/1.8.2/json'
        $oldWheelInfo = @($metadata.urls | Where-Object {$_.filename -eq 'zotero_pdf2zh_pro-1.8.2-py3-none-any.whl'})
        if ($oldWheelInfo.Count -ne 1) { throw 'Expected one published v1.8.2 wheel.' }
        $oldWheel = Join-Path $previous $oldWheelInfo[0].filename
        Invoke-WebRequest $oldWheelInfo[0].url -OutFile $oldWheel -UseBasicParsing
        if ((Get-FileHash -LiteralPath $oldWheel -Algorithm SHA256).Hash -ine $oldWheelInfo[0].digests.sha256) {
            throw 'Previous wheel checksum mismatch.'
        }
        $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',
            (Join-Path $PSScriptRoot 'test_windows_previous_release.ps1'),
            '-PreviousPackage',(Join-Path $previous 'zotero-pdf2zh-pro-windows-x64.zip'),
            '-PreviousWheel',$oldWheel,'-Package',$package,'-Wheel',$wheel,
            '-Root',(Join-Path $fixture 'migration 中文 with spaces'))
        if ($DownloadCache) { $arguments += @('-DownloadCache', $DownloadCache) }
        & powershell @arguments
        if ($LASTEXITCODE -ne 0) { throw 'Previous-release migration validation failed.' }
    }
    @{version=$Version;commit=(& git -C $repo rev-parse HEAD);status='passed';
      lifecycle=(-not $SkipLifecycle);previousRelease=(-not $SkipPreviousRelease);
      packageSha256=(Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant();
      wheelSha256=(Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $fixture 'validation.json') -Encoding utf8
    Write-Host "Extended validation passed; evidence: $fixture/validation.json"
} finally {
    # Also clean up failed/relocated fixtures; never match the production directory.
    $prefix = [IO.Path]::GetFullPath($fixture).TrimEnd('\') + '\'
    foreach ($process in @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
        $_.Name -in @('zotero-pdf2zh-pro.exe','python.exe','pythonw.exe')
    })) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)"
        if ($current -and $current.ExecutablePath -eq $process.ExecutablePath -and $current.CreationDate -eq $process.CreationDate) {
            Stop-Process -Id $current.ProcessId -ErrorAction SilentlyContinue
        }
    }
    if ($registry -notlike 'HKCU:\Software\pdf2zh-release-test-*') { throw 'Unsafe test registry cleanup.' }
    Remove-Item -LiteralPath $registry -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
}

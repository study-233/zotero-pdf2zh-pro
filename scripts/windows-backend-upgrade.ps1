# One-time upgrade of a registered Windows installation using its official release package.
param([Parameter(Mandatory)][string]$InstallRoot,
      [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$TargetVersion)
$ErrorActionPreference = 'Stop'
$registered = (Get-ItemProperty -LiteralPath 'HKCU:\Software\zotero-pdf2zh-pro' -Name InstallRoot).InstallRoot
if (-not [IO.Path]::IsPathRooted($registered) -or [IO.Path]::GetFullPath($registered) -ine [IO.Path]::GetFullPath($InstallRoot)) {
    throw 'Installation registration changed.'
}
$installed = (Get-Content -Raw -LiteralPath (Join-Path $InstallRoot 'installed-version.txt')).Trim()
if ([version]$installed -ge [version]$TargetVersion) { return }
$updates = Join-Path $InstallRoot 'updates'
[void][IO.Directory]::CreateDirectory($updates)
$lock = [IO.File]::Open((Join-Path $updates 'plugin-bootstrap.lock'),'OpenOrCreate','Write','None')
try {
    $base = "https://github.com/study-233/zotero-pdf2zh-pro/releases/download/v$TargetVersion"
    $expected = "$base/zotero-pdf2zh-pro-windows-x64.zip"
    $manifest = Invoke-RestMethod -Uri "$base/windows-update.json" -TimeoutSec 30
    if ($manifest.schemaVersion -ne 1 -or $manifest.version -cne $TargetVersion -or $manifest.url -cne $expected -or
        $manifest.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or $manifest.size -le 0 -or $manifest.size -gt 268435456) {
        throw 'The matching official update package is unavailable.'
    }
    $stage = Join-Path $updates ('plugin-bootstrap-' + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($stage)
    $zipFile = Join-Path $stage 'package.zip'
    Invoke-WebRequest -UseBasicParsing -Uri $expected -OutFile $zipFile -TimeoutSec 300
    if ((Get-Item -LiteralPath $zipFile).Length -ne $manifest.size -or (Get-FileHash -LiteralPath $zipFile -Algorithm SHA256).Hash -ine $manifest.sha256) {
        throw 'Update checksum mismatch.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zipFile)
    try {
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { throw 'Invalid update member.' }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,(Join-Path $stage $entry.FullName))
        }
    } finally { $archive.Dispose() }
    & (Join-Path $stage 'install.ps1') -InstallRoot $InstallRoot -NonInteractive -MinimumIdleSeconds 60
    if ((Get-Content -Raw -LiteralPath (Join-Path $InstallRoot 'installed-version.txt')).Trim() -ne $TargetVersion) {
        throw 'Control center upgrade did not finish.'
    }
    Start-Process -FilePath (Join-Path $InstallRoot 'bin/zotero-pdf2zh-pro.exe') -ArgumentList '--post-install','--keep-stopped' -WindowStyle Hidden
} finally {
    try {
        if ($stage) {
            $owned = Get-Item -LiteralPath $stage -ErrorAction SilentlyContinue
            if ($owned -and $owned.Parent.FullName -ieq [IO.Path]::GetFullPath($updates) -and
                $owned.Name -cmatch '^plugin-bootstrap-[a-f0-9]{32}$' -and
                -not ($owned.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                # This staging area contains only flat archive entries. Never recurse into unexpected directories.
                foreach ($file in $owned.GetFiles()) { $file.Delete() }
                if (-not $owned.GetDirectories().Count) { $owned.Delete() }
            }
        }
    } finally { $lock.Dispose() }
}

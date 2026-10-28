param([switch]$ClearDownloads, [switch]$ReportOnly, [switch]$SkipSpaceReport)
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'runtime.ps1')
$operation = Enter-RuntimeOperation
try {
    if (-not $ReportOnly) {
        $uv = Get-UvExecutable
        if ($uv) { & $uv cache prune; if ($LASTEXITCODE -ne 0) {throw 'uv cache prune failed.'} }
        $active = Get-ServerExecutable
        $protected = @($active)
        $journal = Join-Path $AppRoot 'updates/transaction.json'
        if (Test-Path -LiteralPath $journal) {
            $transaction = Get-Content -Raw -Encoding utf8 -LiteralPath $journal | ConvertFrom-Json
            if ([datetime]$transaction.created -gt (Get-Date).ToUniversalTime().AddDays(-7)) { $protected += $transaction.oldServer }
        }
        $prepared = Join-Path $AppRoot 'updates/prepared.json'
        if (Test-Path -LiteralPath $prepared) {
            $candidate = Get-Content -Raw -Encoding utf8 -LiteralPath $prepared | ConvertFrom-Json
            $protected += Join-Path $candidate.environment "Scripts/$ProductName.exe"
        }
        foreach ($parent in @('runtime/versions', 'runtime/tools', 'updates/previous', 'updates/candidates', 'updates/downloads')) {
            $directory = Join-Path $AppRoot $parent
            if (-not (Test-Path -LiteralPath $directory)) {continue}
            foreach ($entry in @(Get-ChildItem -LiteralPath $directory -Directory)) {
                if ($parent -eq 'runtime/tools' -and $entry.Name -ne $ProductName) {continue}
                if ($entry.LastWriteTimeUtc -gt (Get-Date).ToUniversalTime().AddDays(-7)) {continue}
                $inUse = @($protected | Where-Object {$_ -and $_.StartsWith($entry.FullName + '\', [StringComparison]::OrdinalIgnoreCase)}).Count -gt 0
                $statePath=Join-Path $AppRoot 'updates/state.json'
                if ($parent -eq 'updates/downloads' -and (Test-Path -LiteralPath $statePath)) {
                    $updateState=Get-Content -Raw -Encoding utf8 -LiteralPath $statePath|ConvertFrom-Json
                    if ($updateState.PSObject.Properties.Name -contains 'directory' -and $updateState.phase -in @('ready','preparing') -and $updateState.directory -eq $entry.FullName) {$inUse=$true}
                }
                if ($parent -eq 'updates/candidates' -and (Test-Path -LiteralPath $prepared) -and $candidate.bin.StartsWith($entry.FullName + '\')) {$inUse=$true}
                if (-not $inUse) {
                    $target = Assert-ProductChild $entry.FullName $directory
                    Remove-Item -LiteralPath $target -Recurse -Force
                    if ($parent -eq 'runtime/tools') {
                        $legacyLauncher = Join-Path $UvToolBinDir "$ProductName.exe"
                        if (Test-Path -LiteralPath $legacyLauncher) {
                            Remove-Item -LiteralPath (Assert-ProductChild $legacyLauncher $AppRoot) -Force
                        }
                    }
                    Write-RuntimeLog "Reclaimed expired product directory $target"
                }
            }
        }
        $downloads=Join-Path $AppRoot 'updates/downloads'
        if (Test-Path -LiteralPath $downloads) {
            foreach ($file in @(Get-ChildItem -LiteralPath $downloads -File)) {
                if ($file.LastWriteTimeUtc -lt (Get-Date).ToUniversalTime().AddDays(-7)) {
                    Remove-Item -LiteralPath (Assert-ProductChild $file.FullName $downloads) -Force
                }
            }
        }
        if ($ClearDownloads) {
            $statePath = Join-Path $AppRoot 'updates/state.json'
            $pending = if (Test-Path -LiteralPath $statePath) {Get-Content -Raw -Encoding utf8 -LiteralPath $statePath|ConvertFrom-Json} else {$null}
            if ($pending -and $pending.phase -in @('ready','preparing')) {throw 'An update is pending; its verified downloads cannot be cleared.'}
            $downloads = Join-Path $AppRoot 'updates/downloads'
            if (Test-Path -LiteralPath $downloads) { Remove-Item -LiteralPath (Assert-ProductChild $downloads $AppRoot) -Recurse -Force }
        }
    }
    # Daily cleanup must not hold the operation lock while inspecting every cached file.
    if ($SkipSpaceReport) { return }
    # Deduplicate by NTFS volume/file identity, including links shared by uv and environments.
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class ProductSpace {
    [StructLayout(LayoutKind.Sequential)] struct Info {
        public uint attr; public System.Runtime.InteropServices.ComTypes.FILETIME created,access,written;
        public uint volume,sizeHigh,sizeLow,links,indexHigh,indexLow;
    }
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(IntPtr handle,out Info info);
    public static long[] Measure(string root) {
        long logical=0,unique=0; var seen=new HashSet<string>();
        foreach(var path in Directory.EnumerateFiles(root,"*",SearchOption.AllDirectories)) {
            if (Path.GetFileName(path)==".operation.lock") continue;
            var file=new FileInfo(path); logical+=file.Length;
            using(var stream=File.Open(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)) {
                Info info; if(!GetFileInformationByHandle(stream.SafeFileHandle.DangerousGetHandle(),out info)) throw new IOException("Cannot inspect file identity: "+path);
                if(seen.Add(info.volume+":"+info.indexHigh+":"+info.indexLow)) unique+=file.Length;
            }
        }
        return new long[]{logical,unique};
    }
}
'@
    $sizes = [ProductSpace]::Measure($AppRoot)
    Write-RuntimeLog "Space report logicalBytes=$($sizes[0]); uniqueFileBytes=$($sizes[1]); data/models/fonts preserved."
} finally { $operation.Dispose() }

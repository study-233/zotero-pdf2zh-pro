$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'prepare_windows_build_tools.ps1'
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Cannot parse toolchain verifier' }
$verifier = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-VerifiedArchive' }, $true)
Invoke-Expression $verifier.Extent.Text

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-archive-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$testRoot = [IO.Path]::GetFullPath($testRoot)
$script:downloads = 0
$script:downloadBytes = [Text.Encoding]::UTF8.GetBytes('verified archive fixture')
function Invoke-WebRequest([string]$Uri, [string]$OutFile) {
    $script:downloads++
    [IO.File]::WriteAllBytes($OutFile, $script:downloadBytes)
}
try {
    $archive = Join-Path $testRoot 'archive.bin'
    [IO.File]::WriteAllBytes($archive, $script:downloadBytes)
    $expected = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    Get-VerifiedArchive 'https://example.test/pinned' $archive $expected
    if ($script:downloads -ne 0) { throw 'A verified warm archive must be reused' }
    [IO.File]::WriteAllText($archive, 'tampered cache')
    Get-VerifiedArchive 'https://example.test/pinned' $archive $expected
    if ($script:downloads -ne 1 -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
        throw 'A tampered cached archive must be replaced only by a verified download'
    }
    $script:downloadBytes = [Text.Encoding]::UTF8.GetBytes('tampered download')
    $missing = Join-Path $testRoot 'missing.bin'
    $rejected = $false
    try { Get-VerifiedArchive 'https://example.test/pinned' $missing $expected } catch { $rejected = $true }
    if (-not $rejected -or (Test-Path -LiteralPath $missing)) { throw 'A mismatched download must never become an archive' }
    Write-Host 'Verified warm-cache, poisoned-cache and poisoned-download checks passed'
} finally {
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $testRoot.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Test cleanup escapes temporary directory' }
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}

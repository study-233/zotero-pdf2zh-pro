param([string]$UvExecutable)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -ne 5) { throw "This regression requires Windows PowerShell 5.1." }
if (-not $UvExecutable) { $UvExecutable = (Get-Command uv.exe -ErrorAction Stop).Source }
$windowsDir = Join-Path $PSScriptRoot "windows"
$testParent = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.tmp"))
$testRoot = Join-Path $testParent ("uv-path-test-" + [guid]::NewGuid().ToString("N"))
$savedEnvironment = @{}
foreach ($name in @("PDF2ZH_WINDOWS_APP_ROOT", "PDF2ZH_WINDOWS_REGISTRY_KEY", "UV_INSTALL_DIR", "UV_TOOL_DIR",
        "UV_TOOL_BIN_DIR", "UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR", "UV_NO_CONFIG")) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
$savedConsoleEncoding = [Console]::OutputEncoding
$savedOutputEncoding = $OutputEncoding

function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    if ($Actual -cne $Expected) { throw $Message }
}

try {
    # Keep this source ASCII so PS5.1 does not depend on the script file's BOM/code page.
    $unicodeRoot = Join-Path $testRoot (([char]0x5B89).ToString() + [char]0x88C5 + " root with spaces")
    $privateUv = Join-Path $unicodeRoot "runtime\uv\uv.exe"
    New-Item -ItemType Directory -Force -Path (Split-Path $privateUv -Parent) | Out-Null
    Copy-Item -LiteralPath $UvExecutable -Destination $privateUv
    $env:PDF2ZH_WINDOWS_APP_ROOT = $unicodeRoot
    $env:PDF2ZH_WINDOWS_REGISTRY_KEY = "HKCU:\Software\pdf2zh-uv-path-test-$([guid]::NewGuid().ToString('N'))"
    $env:UV_NO_CONFIG = "true"
    . (Join-Path $windowsDir "common.ps1")

    # Reproduce the production console mismatch without changing the helper's process-wide encoding.
    [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(936)
    $OutputEncoding = [Text.Encoding]::ASCII
    $toolBin = Get-UvToolDirectory -UvExecutable $privateUv -Bin
    $toolRoot = Get-UvToolDirectory -UvExecutable $privateUv
    Assert-Equal $toolBin $UvToolBinDir "Native uv --bin output corrupted a Unicode path."
    Assert-Equal $toolRoot $UvToolsDir "Native uv tool dir output corrupted a Unicode path."
    Assert-Equal ([Console]::OutputEncoding.CodePage) 936 "The helper changed the console output encoding."
    Assert-Equal $OutputEncoding.CodePage 20127 "The helper changed PowerShell output encoding."

    $server = Join-Path $toolBin "$ProductName.exe"
    $python = Join-Path (Join-Path $toolRoot $ProductName) "Scripts\python.exe"
    New-Item -ItemType Directory -Force -Path $toolBin, (Split-Path $python -Parent) | Out-Null
    Set-Content -LiteralPath $server -Value "fixture" -Encoding ascii
    Set-Content -LiteralPath $python -Value "fixture" -Encoding ascii
    Assert-Equal (Get-ServerExecutable) $server "Server discovery did not preserve the Unicode tool-bin path."
    Assert-Equal (Get-ToolPythonExecutable) $python "Python discovery did not preserve the Unicode tool directory."
    # Rust writes UTF-8 state without a BOM; PS5.1 must not decode it using the ANSI code page.
    [IO.File]::WriteAllText($ExecutableFile, $server, (New-Object Text.UTF8Encoding($false)))
    $env:UV_NO_CONFIG = "invalid-bool"
    Assert-Equal (Get-ServerExecutable) $server "Saved UTF-8 launcher path was not restored before uv discovery."
    $env:UV_NO_CONFIG = "true"

    # Execute the real legacy-data function, without invoking the full installer.
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $windowsDir "install.ps1"), [ref]$tokens, [ref]$errors)
    if ($errors) { throw "Cannot parse the production installer." }
    $function = $ast.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq "Copy-LegacyData"
    }, $true)
    if (-not $function) { throw "Production legacy-data function missing." }
    . ([scriptblock]::Create($function.Extent.Text))
    $legacyData = Join-Path (Join-Path $toolRoot $ProductName) "Lib\site-packages\translates"
    New-Item -ItemType Directory -Force -Path $legacyData | Out-Null
    Set-Content -LiteralPath (Join-Path $legacyData "retained.txt") -Value "legacy-fixture" -Encoding ascii
    Copy-LegacyData -UvExecutable $privateUv
    Assert-Equal ((Get-Content -Raw -LiteralPath (Join-Path $DataDir "retained.txt")).Trim()) "legacy-fixture" "Legacy data was not copied from the Unicode tool directory."

    # Use a real uv option parse failure; do not let an error become a directory string.
    $env:UV_NO_CONFIG = "invalid-bool"
    $rejected = $false
    try { Get-UvToolDirectory -UvExecutable $privateUv -Bin | Out-Null } catch {
        $rejected = $_.Exception.Message -match "exit code 2" -and $_.Exception.Message -match "invalid-bool"
    }
    if (-not $rejected) { throw "Native uv failure or its UTF-8 stderr was discarded." }
    $env:UV_NO_CONFIG = "true"

    # A separate native empty-output fixture covers the guard that valid uv does not trigger.
    $emptyExe = Join-Path $testRoot "empty-output.exe"
    Add-Type -TypeDefinition 'public static class EmptyUvPathOutput { public static int Main(string[] args) { if (args.Length > 0 && args[0] == "hold") System.Threading.Thread.Sleep(30000); return 0; } }' `
        -OutputAssembly $emptyExe -OutputType ConsoleApplication
    $rejected = $false
    try { Get-UvToolDirectory -UvExecutable $emptyExe | Out-Null } catch {
        $rejected = $_.Exception.Message -match "returned an empty directory"
    }
    if (-not $rejected) { throw "An empty native directory result was accepted." }
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    Copy-Item -LiteralPath $emptyExe -Destination $ControlPanelExecutable
    $control = Start-Process -FilePath $ControlPanelExecutable -ArgumentList "hold" -WindowStyle Hidden -PassThru
    try {
        [IO.File]::WriteAllText($ControlPanelExecutableFile, $ControlPanelExecutable, (New-Object Text.UTF8Encoding($false)))
        Set-Content -LiteralPath $ControlPanelPidFile -Value $control.Id -Encoding ascii
        Assert-Equal (Get-ManagedControlPanelProcessId) $control.Id "Rust UTF-8 process state did not resolve the real Unicode executable."
    } finally {
        if (-not $control.HasExited) { $control.Kill(); $control.WaitForExit() }
        $control.Dispose()
    }
    Assert-Equal ([Console]::OutputEncoding.CodePage) 936 "Failure handling changed the console encoding."
    Assert-Equal $OutputEncoding.CodePage 20127 "Failure handling changed PowerShell output encoding."
    Write-Host "Real uv UTF-8 paths, Unicode server/Python discovery and process state, legacy data, native failure and empty-output guards passed in PowerShell 5.1."
} finally {
    [Console]::OutputEncoding = $savedConsoleEncoding
    $OutputEncoding = $savedOutputEncoding
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
    }
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolvedTestRoot.StartsWith($testParent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path $resolvedTestRoot -Leaf) -notlike "uv-path-test-*") {
        throw "Refusing to remove an unexpected uv path fixture directory."
    }
    if (Test-Path -LiteralPath $resolvedTestRoot) {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}

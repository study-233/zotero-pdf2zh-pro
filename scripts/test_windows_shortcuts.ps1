Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$windowsDir = Join-Path $PSScriptRoot "windows"
$testParent = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.tmp"))
$testRoot = Join-Path $testParent ("shortcut-test-" + [guid]::NewGuid().ToString("N"))
$savedEnvironment = @{}
foreach ($name in @("PDF2ZH_WINDOWS_APP_ROOT", "PDF2ZH_WINDOWS_START_MENU_DIR", "PDF2ZH_WINDOWS_REGISTRY_KEY")) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

function Import-TestedFunction {
    param([string]$Path, [string]$Name)
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors) { throw "Cannot parse $Path" }
    $functions = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name
    }, $true))
    if ($functions.Count -ne 1) { throw "Expected one production function named $Name" }
    return [scriptblock]::Create($functions[0].Extent.Text)
}

function Assert-ShortcutRoot {
    param([string]$Root)
    $gui = Join-Path (Join-Path $Root "bin") "$ProductName.exe"
    $shell = New-Object -ComObject WScript.Shell
    foreach ($name in @("$ProductName.lnk", "Uninstall.lnk")) {
        $path = Join-Path $StartMenuDir $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing shortcut: $name" }
        $shortcut = $shell.CreateShortcut($path)
        $target = if ($name -eq "Uninstall.lnk") { Join-Path (Join-Path $Root "bin") "uninstall.cmd" } else { $gui }
        if ($shortcut.TargetPath -ne $target) { throw "Wrong shortcut target: $name" }
        if ($shortcut.WorkingDirectory -ne $Root) { throw "Wrong shortcut working directory: $name" }
        if ($shortcut.IconLocation -ne "$gui,0") { throw "Wrong shortcut icon: $name" }
    }
}

function Assert-Refresh {
    param([string]$Root, [bool]$WithoutShortcuts = $false)
    $last = $script:ShellRefreshCalls[$script:ShellRefreshCalls.Count - 1]
    if ($last.Root -ne $Root -or $last.NoShortcuts -ne $WithoutShortcuts) {
        throw "Shell notification targeted the wrong installation or shortcut set."
    }
}

try {
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    $sourceRoot = Join-Path $testRoot (([char]0x4E2D).ToString() + [char]0x6587 + " source with spaces")
    $destinationRoot = Join-Path $testRoot "destination with spaces"
    foreach ($root in @($sourceRoot, $destinationRoot)) {
        $bin = Join-Path $root "bin"
        New-Item -ItemType Directory -Path $bin -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $env:SystemRoot "System32\cmd.exe") -Destination (Join-Path $bin "zotero-pdf2zh-pro.exe")
        Set-Content -LiteralPath (Join-Path $bin "uninstall.cmd") -Value "@exit /b 0" -Encoding ascii
    }
    $env:PDF2ZH_WINDOWS_APP_ROOT = $sourceRoot
    $env:PDF2ZH_WINDOWS_START_MENU_DIR = Join-Path $testRoot "start menu"
    $env:PDF2ZH_WINDOWS_REGISTRY_KEY = "HKCU:\Software\pdf2zh-shortcut-test-$([guid]::NewGuid().ToString('N'))"
    . (Join-Path $windowsDir "common.ps1")
    . (Import-TestedFunction -Path (Join-Path $windowsDir "install.ps1") -Name "Install-Shortcuts")
    . (Import-TestedFunction -Path (Join-Path $windowsDir "relocate.ps1") -Name "Set-ProductShortcuts")

    # Record the production caller's target while still invoking the real native refresh.
    $script:NativeShellRefresh = (Get-Item Function:Update-ProductShellIcons).ScriptBlock
    $script:ShellRefreshCalls = [Collections.ArrayList]::new()
    function Update-ProductShellIcons {
        param([string]$Root = $AppRoot, [switch]$NoShortcuts)
        [void]$script:ShellRefreshCalls.Add(@{ Root = $Root; NoShortcuts = [bool]$NoShortcuts })
        & $script:NativeShellRefresh -Root $Root -NoShortcuts:$NoShortcuts
    }

    $NoShortcuts = $false
    Install-Shortcuts
    Assert-ShortcutRoot $sourceRoot
    Assert-Refresh $sourceRoot

    # An upgrade must repair an existing explicit icon from another executable.
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $StartMenuDir "$ProductName.lnk"))
    $shortcut.IconLocation = (Join-Path $destinationRoot "bin\$ProductName.exe") + ",0"
    $shortcut.Save()
    Install-Shortcuts
    Assert-ShortcutRoot $sourceRoot
    Assert-Refresh $sourceRoot

    Set-ProductShortcuts -Root $destinationRoot
    Assert-ShortcutRoot $destinationRoot
    Assert-Refresh $destinationRoot

    # Preparation at another root must not redirect the current Start menu links.
    $NoShortcuts = $true
    Install-Shortcuts
    Assert-ShortcutRoot $destinationRoot
    Assert-Refresh -Root $sourceRoot -WithoutShortcuts $true

    # Relocation rollback uses this same production function with the source root.
    Set-ProductShortcuts -Root $sourceRoot
    Assert-ShortcutRoot $sourceRoot
    Assert-Refresh $sourceRoot
    if ($script:ShellRefreshCalls.Count -ne 5) { throw "Unexpected shell refresh count." }
    Write-Host "Windows shortcut targets/icons and targeted native refresh passed for install, upgrade, relocation and source restoration."
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
    }
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolvedTestRoot.StartsWith($testParent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a shortcut test directory outside the workspace test parent."
    }
    if (Test-Path -LiteralPath $resolvedTestRoot) {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}

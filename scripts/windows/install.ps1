param(
    [string]$PackageSource, [string]$GuiSource, [string]$InstallRoot,
    [switch]$PrepareOnly, [switch]$Repair, [switch]$DeferLocationCommit,
    [switch]$AllowPreparedDestination, [switch]$SkipUvBootstrap,
    [switch]$NoShortcuts, [switch]$NonInteractive
)
if ($InstallRoot) {
    if (-not [IO.Path]::IsPathRooted($InstallRoot)) {throw 'An absolute installation path is required.'}
    $env:PDF2ZH_WINDOWS_APP_ROOT=[IO.Path]::GetFullPath($InstallRoot)
}
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'runtime.ps1')
Assert-WindowsX64
function Copy-LegacyData {
    param([string]$UvExecutable)
    if ((Test-Path -LiteralPath $DataDir) -and @(Get-ChildItem -LiteralPath $DataDir -Force).Count) {return}
    $legacy=Join-Path (Get-UvToolDirectory -UvExecutable $UvExecutable) "$ProductName/Lib/site-packages/translates"
    if (Test-Path -LiteralPath $legacy) {
        New-Item -ItemType Directory -Force -Path $DataDir|Out-Null
        Copy-Item -Path (Join-Path $legacy '*') -Destination $DataDir -Recurse -Force
        Write-Host "Copied legacy task data to $DataDir"
    }
}
$known=@('bin','cache','data','logs','runtime','updates','settings.json','install-state.json','.operation.lock','installed-version.txt',
    'server.pid','server-executable.txt','control-panel.pid','control-panel-executable.txt','last-operation-error.txt','candidate-profile.json')
if (Test-Path -LiteralPath $AppRoot) {
    if (-not (Test-Path -LiteralPath $AppRoot -PathType Container)) {throw 'Installation path points to a file.'}
    $entries=@(Get-ChildItem -LiteralPath $AppRoot -Force)
    if (@($entries|Where-Object {$_.Name -notin $known}).Count) {throw 'Installation path contains unrelated files.'}
    $ownedPreparation=$false
    $ownershipPath=Join-Path $AppRoot 'install-state.json'
    if (Test-Path -LiteralPath $ownershipPath) {
        $ownership=Get-Content -Raw -Encoding utf8 -LiteralPath $ownershipPath|ConvertFrom-Json
        $ownedPreparation=$ownership.product -eq $ProductName -and (Test-PathEqual $ownership.root $AppRoot)
    }
    if ($entries.Count -and -not $ownedPreparation -and -not (Test-ManagedInstallRoot $AppRoot) -and -not (Test-PreparationOnlyRoot $AppRoot) -and -not $AllowPreparedDestination) {
        $saved=Get-SavedInstallRoot
        if (-not (Test-PathEqual $saved $AppRoot) -or @($entries|Where-Object {$_.Name -notin @('data','logs','settings.json','.operation.lock')}).Count) {throw 'Installation path is not a recognized product directory.'}
    }
}
if (-not $GuiSource) {$GuiSource=Join-Path $PSScriptRoot "$ProductName.exe"}
if (-not (Test-Path -LiteralPath $GuiSource -PathType Leaf)) {throw 'The control center executable is missing.'}
if ([Diagnostics.FileVersionInfo]::GetVersionInfo($GuiSource).FileVersion -ne $PackageVersion) {throw 'The control center version does not match the package.'}
$manifest=Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $PSScriptRoot 'windows-dependencies.json')|ConvertFrom-Json
$requirements=Join-Path $PSScriptRoot 'windows-requirements.txt'
if ($manifest.version -ne $PackageVersion -or (Get-ProductFileSha256 $requirements) -ine $manifest.requirementsSha256) {throw 'The dependency lock is damaged or belongs to another version.'}
if ((Test-Path -LiteralPath $InstalledVersionFile) -and [version](Get-Content -Raw -LiteralPath $InstalledVersionFile).Trim() -gt [version]$PackageVersion) {throw 'Downgrade blocked.'}
$operation=Enter-RuntimeOperation
try {
    New-Item -ItemType Directory -Force -Path $DataDir,$LogsDir,(Join-Path $AppRoot 'updates/downloads'),(Join-Path $RuntimeDir 'versions')|Out-Null
    Write-AtomicJson (Join-Path $AppRoot 'install-state.json') @{product=$ProductName;root=$AppRoot}
    Write-OperationProgress 'checking' '正在检查安装环境'
    $preparationError=Join-Path $AppRoot 'updates/preparation-error.json'
    Remove-Item -LiteralPath $preparationError -Force -ErrorAction SilentlyContinue
    $script:RuntimeFailureCategory='install'
    Use-PrivateUvEnvironment
    $script:RuntimeUv=Get-UvExecutable
    if (-not (Test-Path -LiteralPath $PrivateUvExecutable)) {
        New-Item -ItemType Directory -Force -Path $UvInstallDir|Out-Null
        if ($script:RuntimeUv -and $SkipUvBootstrap) {Copy-Item -LiteralPath $script:RuntimeUv -Destination $PrivateUvExecutable}
        else {
            $archive=Join-Path $AppRoot "updates/downloads/uv-$($manifest.uvVersion).zip"
            if (-not (Test-Path -LiteralPath $archive)) {
                Save-ProgressDownload -Uri "https://github.com/astral-sh/uv/releases/download/$($manifest.uvVersion)/uv-x86_64-pc-windows-msvc.zip" -Destination "$archive.part" -Message '正在下载安装工具'
                Move-Item -LiteralPath "$archive.part" -Destination $archive -Force
                Write-RuntimeLog "Downloaded uv bytes=$((Get-Item -LiteralPath $archive).Length); reason=missing runtime."
            }
            Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $UvInstallDir 'bootstrap') -Force
            $binary=Get-ChildItem -LiteralPath (Join-Path $UvInstallDir 'bootstrap') -Recurse -Filter uv.exe|Select-Object -First 1
            if (-not $binary -or (Get-ProductFileSha256 $binary.FullName) -ine $manifest.uvBinarySha256) {
                Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
                throw 'uv bootstrap checksum failed; retry to download again.'
            }
            Copy-Item -LiteralPath $binary.FullName -Destination $PrivateUvExecutable -Force
        }
    }
    $script:RuntimeUv=$PrivateUvExecutable
    Write-OperationProgress 'files' '正在准备服务程序'
    Write-RuntimeLog "Reusing uv=$script:RuntimeUv; Python=$($manifest.python); cache=$UvCacheDir; link-mode=hardlink."
    Copy-LegacyData -UvExecutable $script:RuntimeUv
    $wheel=Join-Path $AppRoot ('updates/downloads/'+$manifest.serverWheel.name)
    if (-not $PackageSource) {
        $sidecar=Join-Path $PSScriptRoot $manifest.serverWheel.name
        if (Test-Path -LiteralPath $sidecar -PathType Leaf) {$PackageSource=$sidecar}
    }
    if ($PackageSource) {
        if ((Get-ProductFileSha256 $PackageSource) -ine $manifest.serverWheel.sha256) {throw 'The supplied service wheel does not match the release checksum.'}
        if (-not (Test-PathEqual $PackageSource $wheel)) {Copy-Item -LiteralPath $PackageSource -Destination $wheel -Force}
    }
    $valid=(Test-Path -LiteralPath $wheel) -and ((Get-ProductFileSha256 $wheel) -ieq $manifest.serverWheel.sha256)
    if (-not $valid) {
        $metadata=Invoke-RestMethod -Uri "https://pypi.org/pypi/$ProductName/$PackageVersion/json" -TimeoutSec 30
        $asset=@($metadata.urls|Where-Object {$_.filename -eq $manifest.serverWheel.name -and $_.digests.sha256 -eq $manifest.serverWheel.sha256})
        if ($asset.Count -ne 1 -or -not $asset[0].url.StartsWith('https://files.pythonhosted.org/')) {throw 'The service release checksum does not match PyPI.'}
        Save-ProgressDownload -Uri $asset[0].url -Destination "$wheel.part" -Message '正在下载翻译服务程序'
        if ((Get-ProductFileSha256 "$wheel.part") -ine $manifest.serverWheel.sha256) {throw 'The service download is damaged.'}
        Move-Item -LiteralPath "$wheel.part" -Destination $wheel -Force
        Write-RuntimeLog "Downloaded service bytes=$((Get-Item -LiteralPath $wheel).Length); reason=new service package."
    } else {
        Write-RuntimeLog 'Cache hit: verified service wheel; bytes=0.'
        Write-OperationProgress 'files' '服务程序已复用'
    }
    $identity="$PackageVersion-py$($manifest.python)-$($manifest.requirementsSha256.Substring(0,12))-$($manifest.serverWheel.sha256.Substring(0,8))"
    if ($Repair) {$identity+='-'+[guid]::NewGuid().ToString('N').Substring(0,8)}
    $environment=Join-Path $RuntimeDir "versions/$identity"
    $marker=Join-Path $environment '.prepared.json';$python=Join-Path $environment 'Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $marker)) {
        if (Test-Path -LiteralPath $environment) {
            $environment+='-'+[guid]::NewGuid().ToString('N').Substring(0,8)
            $marker=Join-Path $environment '.prepared.json';$python=Join-Path $environment 'Scripts/python.exe'
        }
        Write-OperationProgress 'python' '正在准备 Python'
        Invoke-Uv -Arguments @('venv','--python',$manifest.python,'--managed-python','--no-config',$environment)
        Write-OperationProgress 'dependencies' '正在准备依赖'
        Invoke-Uv -Arguments @('pip','install','--python',$python,'--no-deps','--require-hashes','--no-config','--link-mode','hardlink','--default-index','https://pypi.org/simple','-r',$requirements)
        Invoke-Uv -Arguments @('pip','install','--python',$python,'--no-deps','--no-config','--link-mode','hardlink',$wheel)
        # Import validation must not open the live/default task store while the
        # current service is still running. Health checks use the real data later.
        & $python -c "import os, sys; os.environ['PDF2ZH_DATA_DIR']=sys.argv[2]; import server, maintenance; assert server.VERSION == sys.argv[1]" $PackageVersion (Join-Path $environment '.validation-data')
        if ($LASTEXITCODE -ne 0) {throw 'Candidate failed import/version validation.'}
        Write-AtomicJson $marker @{version=$PackageVersion;python=$manifest.python;requirementsSha256=$manifest.requirementsSha256;wheelSha256=$manifest.serverWheel.sha256}
    } else {
        Invoke-Uv -Arguments @('pip','check','--python',$python)
        Write-RuntimeLog 'Cache hit: candidate environment; verifying installed hashes.'
    }
    Write-OperationProgress 'verifying' '正在校验服务与依赖文件'
    $damaged=@(& $python (Join-Path $PSScriptRoot 'verify-runtime.py'))
    if ($LASTEXITCODE -eq 2) {
        if (-not $damaged.Count) {throw 'Runtime verification did not identify the damaged package.'}
        Write-RuntimeLog "Supplemental download reason=missing/corrupt package; packages=$($damaged -join ',')."
        Write-OperationProgress 'dependencies' '正在补齐缺失或损坏的依赖'
        Invoke-Uv -Arguments (@('cache','clean')+$damaged)
        $arguments=@('pip','install','--python',$python,'--no-deps','--require-hashes','--no-config','--link-mode','hardlink','-r',$requirements)
        foreach ($name in $damaged) {if ($name -ne $ProductName) {$arguments+=@('--reinstall-package',$name)}}
        Invoke-Uv -Arguments $arguments
        if ($damaged -contains $ProductName) {Invoke-Uv -Arguments @('pip','install','--python',$python,'--no-deps','--reinstall-package',$ProductName,$wheel)}
        & $python (Join-Path $PSScriptRoot 'verify-runtime.py')
        if ($LASTEXITCODE -ne 0) {throw 'Repaired dependencies still failed verification.'}
    } elseif ($LASTEXITCODE -ne 0) {throw 'Dependency verification failed.'}
    $candidate=Join-Path $AppRoot ('updates/candidates/'+[guid]::NewGuid().ToString('N')+'/bin')
    New-Item -ItemType Directory -Force -Path $candidate|Out-Null
    foreach ($file in @('common.ps1','runtime.ps1','recover-install.ps1','clean-cache.ps1','verify-runtime.py','windows-dependencies.json','windows-requirements.txt',
        'apply-update.ps1','relocate.ps1','install.ps1','start-server.ps1','stop-server.ps1','view-log.ps1','uninstall.ps1',
        'install.cmd','start-server.cmd','stop-server.cmd','view-log.cmd','uninstall.cmd','LICENSE.txt','THIRD_PARTY_NOTICES.md')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $candidate $file)
    }
    Copy-Item -LiteralPath $GuiSource -Destination (Join-Path $candidate "$ProductName.exe")
    Write-AtomicJson (Join-Path $AppRoot 'updates/prepared.json') @{version=$PackageVersion;environment=$environment;bin=$candidate;created=(Get-Date).ToUniversalTime().ToString('o')}
    Write-RuntimeLog "Prepared $PackageVersion; old service stayed available."
    Write-OperationProgress 'waiting' '新版已准备，等待空闲后切换' -Status 'waiting'
    if (-not $PrepareOnly) {Apply-PreparedRuntime -AllowLegacy -DeferLocationCommit:$DeferLocationCommit -NoShortcuts:$NoShortcuts}
} catch {
    $failure=$_
    if ($failure.Exception -is [OperationCanceledException]) {
        Write-RuntimeLog 'Installation cancelled; verified downloads and existing data were retained.'
        Write-OperationProgress 'cancelled' '安装已取消，已下载的文件可在重试时复用。' -Status 'cancelled'
        exit 1223
    }
    if ($failure.Exception -is [Net.WebException]) {$script:RuntimeFailureCategory='network'}
    Write-AtomicJson (Join-Path $AppRoot 'updates/preparation-error.json') @{category=$script:RuntimeFailureCategory;error=$failure.Exception.Message}
    Write-OperationProgress 'failed' $failure.Exception.Message -Status 'failed'
    throw $failure
} finally {$operation.Dispose()}
if (-not $PrepareOnly -and -not $NonInteractive) {
    Start-Process -FilePath $ControlPanelExecutable -ArgumentList '--post-install','--keep-stopped' -WindowStyle Hidden
}

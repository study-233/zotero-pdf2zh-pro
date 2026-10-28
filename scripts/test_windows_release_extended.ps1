param(
    [Parameter(Mandatory=$true)][string]$Version,
    [switch]$SkipLifecycle,
    [switch]$SkipPreviousRelease,
    [switch]$RecentOnly,
    [switch]$SkipRecentUpgrade,
    [string]$Plan = $env:PDF2ZH_VALIDATION_PLAN,
    [string]$Receipt,
    [string]$DownloadCache = $env:PDF2ZH_TEST_DOWNLOAD_CACHE,
    [string]$SourceCommit
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'test_windows_process_helpers.ps1')
. (Join-Path $PSScriptRoot 'workspace.ps1')
$validationCommit = (& git -C $repo rev-parse HEAD)
if (-not $SourceCommit) { $SourceCommit = $validationCommit }
if ($env:PDF2ZH_VALIDATION_COMMIT) {$validationCommit=$env:PDF2ZH_VALIDATION_COMMIT}
if ($SourceCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'Expected a full source commit.' }
& git -C $repo merge-base --is-ancestor $SourceCommit $validationCommit
if ($LASTEXITCODE -ne 0) { throw 'The source commit must be an ancestor of the validation scripts.' }
$workspaceRun = New-WorkspaceRun -Name 'release-extended'
$fixture = $workspaceRun.work
$passed = $false
$testFailure=$null
$result=$null
$results=@()
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
function Save-SelectedValidation {
    $result.cases=@($results)
    $result|ConvertTo-Json -Depth 12|Set-Content -Encoding utf8 -LiteralPath (Join-Path $workspaceRun.report 'validation.json')
    if ($Receipt) {$result|ConvertTo-Json -Depth 12|Set-Content -Encoding utf8 -LiteralPath $Receipt}
}
try {
    if (-not $Plan) {
        $Plan=Join-Path $workspaceRun.report 'validation-plan.json'
        $planArguments=@($Version,$SourceCommit,$Plan)
        if (-not $RecentOnly) {$planArguments+='--full'}
        & uv run --no-project python (Join-Path $PSScriptRoot 'release_control.py') plan @planArguments
        if ($LASTEXITCODE -ne 0) {throw 'Cannot resolve a frozen validation plan.'}
    }
    $testPlan=Get-Content -Raw -Encoding utf8 -LiteralPath $Plan|ConvertFrom-Json
    $releasePolicy=Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $PSScriptRoot 'release-policy.json')|ConvertFrom-Json
    if ($testPlan.version -ne $Version -or $testPlan.sourceCommit -ne $SourceCommit) {throw 'Validation plan does not match the candidate.'}
    $harnessSha=& uv run --no-project python (Join-Path $PSScriptRoot 'release_control.py') harness-digest
    if ($LASTEXITCODE -ne 0 -or $harnessSha -ne $testPlan.harnessSha256) {throw 'Validation scripts differ from the frozen plan.'}
    $planSha=& uv run --no-project python (Join-Path $PSScriptRoot 'release_control.py') plan-digest $Plan
    if ($LASTEXITCODE -ne 0) {throw 'Cannot identify the validation plan.'}
    $caseDirectory=if ($Receipt) {$Receipt+'.cases'} else {Join-Path $workspaceRun.report 'cases'}
    New-Item -ItemType Directory -Force -Path $caseDirectory|Out-Null
    $result=@{schemaVersion=2;version=$Version;commit=$SourceCommit;validationCommit=$validationCommit;status='running';
      lifecycle=(-not $SkipLifecycle);cases=@();planSha256=$planSha;harnessSha256=$harnessSha;
      packageSha256=(Get-TestFileSha256 $package);wheelSha256=(Get-TestFileSha256 $wheel)}
    Save-SelectedValidation
    if (-not $SkipLifecycle) {
        if ($DownloadCache) {
            $cache = (Resolve-Path -LiteralPath $DownloadCache).Path
            if (-not $cache.StartsWith((Join-Path $repo '.tmp')+'\', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Only an isolated repository download cache may be reused.'
            }
            New-Item -ItemType Directory -Force -Path (Join-Path $env:PDF2ZH_WINDOWS_APP_ROOT 'runtime') | Out-Null
            & python (Join-Path $PSScriptRoot 'release_local.py') seed-cache $cache $env:PDF2ZH_WINDOWS_APP_ROOT
            if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare isolated lifecycle download cache.' }
            $env:PDF2ZH_WINDOWS_LIFECYCLE_PREPARED_CACHE = '1'
        }
        $lifecycleArguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',
            (Join-Path $PSScriptRoot 'test_windows_lifecycle.ps1'),'-WindowsPackage',$package,'-PackageSource',$wheel)
        if ($testPlan.lifecycle -eq 'standard') {$lifecycleArguments+='-SmokeOnly'}
        & powershell @lifecycleArguments
        if ($LASTEXITCODE -ne 0) { throw 'Extended lifecycle validation failed.' }
    }
    foreach ($case in $testPlan.cases) {
        if ($RecentOnly -and $case.id -ne 'recent-upgrade') {continue}
        if ($SkipRecentUpgrade -and $case.id -eq 'recent-upgrade') {continue}
        if ($SkipPreviousRelease -and $case.id -eq 'legacy-upgrade') {continue}
        $caseReceipt=Join-Path $caseDirectory ($case.id+'-'+$case.baseline+'.json')
        $verification=@('run','--no-project','python',(Join-Path $PSScriptRoot 'release_control.py'),'verify-case',
          $Plan,$case.id,$case.baseline,$caseReceipt,$package,$wheel)
        if (Test-Path -LiteralPath $caseReceipt) {
            $cached=& uv @verification
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Reusing passed case $($case.id): $($case.baseline) -> $Version"
                $results+=@{id=$case.id;baseline=$case.baseline;status='passed';receipt=($cached|ConvertFrom-Json)}
                Save-SelectedValidation
                continue
            }
        }
        Write-Host "Starting case $($case.id): $($case.baseline) -> $Version; receipt=$caseReceipt"
        $previous=Join-Path $fixture $case.baseline
        $info=& uv run --no-project python (Join-Path $PSScriptRoot 'release_control.py') baseline $Plan $case.baseline $previous
        if ($LASTEXITCODE -ne 0) {throw "Baseline setup failed: $($case.id) $($case.baseline)"}
        $old=$info|ConvertFrom-Json
        $legacy=$case.baseline -eq $releasePolicy.legacyBaseline
        $scenario=if ($legacy) {'legacy-upgrade'} else {$case.id}
        $descriptor=$releasePolicy.scenarios.PSObject.Properties|Where-Object {$_.Name -eq $scenario}
        if (-not $descriptor) {throw "Unknown validation scenario: $($case.id)"}
        $entry=Join-Path $repo $descriptor.Value.entry
        $arguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$entry,
            '-PreviousPackage',$old.package,'-PreviousWheel',$old.wheel,'-Package',$package,'-Wheel',$wheel,
            '-Receipt',$caseReceipt,'-PlanSha256',$planSha,'-HarnessSha256',$harnessSha,'-SourceCommit',$SourceCommit)
        if ($legacy) {
            $arguments+=@('-Constraints',$old.constraints)
            if ($DownloadCache) {$arguments+=@('-DownloadCache',$DownloadCache)}
        } else {
            $arguments+=@('-PreviousVersion',$case.baseline,'-Version',$Version)
            if ($DownloadCache) {$arguments+=@('-DownloadCache',$DownloadCache)}
        }
        & (Join-Path $PSHOME 'powershell.exe') @arguments
        if ($LASTEXITCODE -ne 0) {throw "Validation failed: $($case.id) $($case.baseline) -> $Version"}
        $verified=& uv @verification
        if ($LASTEXITCODE -ne 0) {throw 'Completed upgrade receipt did not match its frozen identity.'}
        $results+=@{id=$case.id;baseline=$case.baseline;status='passed';receipt=($verified|ConvertFrom-Json)}
        Save-SelectedValidation
    }
    $result.status='passed'
    Save-SelectedValidation
    Write-Host "Selected validation passed; evidence: $($workspaceRun.report)/validation.json"
    $passed = $true
} catch {
    $testFailure=$_
    if ($result) {
        $result.status='failed'; $result.failure=$_.ToString()
        try {Save-SelectedValidation} catch {Write-Warning "Cannot save failed validation: $_"}
    }
    throw
} finally {
    # Also clean up failed/relocated fixtures; never match the production directory.
    $cleanupFailure=$null
    try {
        Stop-IsolatedTestProcesses -Roots @($fixture)
        if ($registry -notlike 'HKCU:\Software\pdf2zh-release-test-*') { throw 'Unsafe test registry cleanup.' }
        Remove-Item -LiteralPath $registry -Recurse -Force -ErrorAction SilentlyContinue
    } catch {$cleanupFailure=$_;Write-Warning "Test workspace retained: $_"}
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    if (-not $cleanupFailure) {Complete-WorkspaceRun -Run $workspaceRun -Passed $passed}
    elseif (-not $testFailure) {throw $cleanupFailure}
}

# Isolated development only. Never invoke scripts/windows installation helpers.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$devPython = Join-Path $repoRoot '.local-dev\runtime\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $devPython -PathType Leaf)) {
    $devPython = (Get-Command python -ErrorAction Stop).Source
}
# -X affects this child only; do not alter the user's environment or execution policy.
& $devPython -X utf8 (Join-Path $PSScriptRoot 'dev.py') @args
exit $LASTEXITCODE

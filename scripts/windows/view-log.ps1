. (Join-Path $PSScriptRoot "common.ps1")

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
if (-not (Test-Path -LiteralPath $LogFile)) {
    New-Item -ItemType File -Force -Path $LogFile | Out-Null
}
Start-Process -FilePath "notepad.exe" -ArgumentList ('"{0}"' -f $LogFile)

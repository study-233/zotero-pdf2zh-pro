param(
    [string]$LogPath,
    [switch]$FunctionsOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-WebView2Runtime {
    param([scriptblock]$ReadVersion = {
        param($Path)
        Get-ItemPropertyValue -LiteralPath $Path -Name pv -ErrorAction Stop
    })
    $client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($key in @(
        "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$client",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$client",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$client"
    )) {
        try {
            $text = ([string](& $ReadVersion $key)).Trim()
            if ($text -notmatch '^[0-9]+(\.[0-9]+){1,3}$') { continue }
            $version = [version]$text
            if ($version -gt [version]'0.0.0.0') { return $true }
        } catch {
            # Missing, inaccessible and malformed registrations are not success.
        }
    }
    return $false
}

function Assert-MicrosoftSignature {
    param($Signature)
    if ($Signature.Status -ne 'Valid' -or $null -eq $Signature.SignerCertificate -or
        $Signature.SignerCertificate.Subject -notmatch '(^|,\s*)O="?Microsoft Corporation"?(,|$)') {
        throw '运行环境安装程序的微软数字签名验证失败，已停止安装。'
    }
}

function Write-PreparationLog {
    param([string]$Path, [string]$Message)
    try {
        $parent = Split-Path -Parent $Path
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Add-Content -LiteralPath $Path -Value "[$([DateTime]::Now.ToString('s'))] $Message" -Encoding UTF8
    } catch {
        # A logging failure must not prevent recovery.
    }
}

function Save-WebView2Bootstrapper {
    param([string]$Destination, [hashtable]$State)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $request = [Net.HttpWebRequest]::Create('https://go.microsoft.com/fwlink/p/?LinkId=2124703')
    $request.Timeout = 120000
    $request.ReadWriteTimeout = 30000
    $request.MaximumAutomaticRedirections = 5
    if ($request.Proxy) { $request.Proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials }
    $response = $null
    $inputStream = $null
    $outputStream = $null
    try {
        $response = $request.GetResponse()
        if ($response.ResponseUri.Scheme -ne 'https') { throw '下载地址未使用 HTTPS。' }
        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew)
        $buffer = New-Object byte[] 65536
        $downloaded = 0L
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($watch.Elapsed.TotalSeconds -gt 180) { throw '下载超时，请检查网络后重试。' }
            $downloaded += $count
            if ($downloaded -gt 50MB) { throw '安装程序大小异常，已停止下载。' }
            $outputStream.Write($buffer, 0, $count)
            $State.Message = '正在下载运行环境安装程序… {0:N1} MB' -f ($downloaded / 1MB)
        }
        if ($downloaded -eq 0) { throw '下载内容为空，请重试。' }
    } finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

function Invoke-WebView2Preparation {
    param(
        [hashtable]$State, [string]$DiagnosticPath,
        [scriptblock]$Detect = { Test-WebView2Runtime },
        [scriptblock]$Download = { param($Path, $Progress) Save-WebView2Bootstrapper $Path $Progress },
        [scriptblock]$Verify = { param($Path) Assert-MicrosoftSignature (Get-AuthenticodeSignature -LiteralPath $Path) },
        [scriptblock]$Pause = { Start-Sleep -Seconds 1 },
        [scriptblock]$Install = {
            param($Path, $Progress)
            $start = [Diagnostics.ProcessStartInfo]::new()
            $start.FileName = $Path
            $start.Arguments = '/silent /install'
            $start.UseShellExecute = $false
            $process = [Diagnostics.Process]::Start($start)
            $Progress.Installer = $process
            if (-not $process.WaitForExit(600000)) {
                throw '安装等待超时。安装程序仍在运行，请等待其结束后重试。'
            }
            if ($process.ExitCode -notin @(0, 3010)) {
                throw "微软安装程序返回错误 $($process.ExitCode)，请检查网络或系统安装限制。"
            }
        }
    )
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('pdf2zh-webview2-' + [guid]::NewGuid().ToString('N'))
    try {
        if (& $Detect) { $State.Status = 'ready'; return }
        Write-PreparationLog $DiagnosticPath 'WebView2 Runtime missing; preparing per-user installation.'
        New-Item -ItemType Directory -Path $temporary | Out-Null
        $installer = Join-Path $temporary 'MicrosoftEdgeWebview2Setup.exe'
        $State.Message = '正在下载运行环境安装程序…'
        & $Download $installer $State
        $State.Message = '正在验证微软数字签名…'
        & $Verify $installer
        $State.Message = '正在安装 Microsoft Edge WebView2…首次准备可能需要几分钟。'
        & $Install $installer $State
        # The updater may finish writing registration shortly after setup exits.
        for ($attempt = 0; $attempt -lt 15; $attempt++) {
            if (& $Detect) {
                Write-PreparationLog $DiagnosticPath 'WebView2 Runtime is ready.'
                $State.Status = 'ready'
                return
            }
            & $Pause
        }
        throw '安装后仍未检测到运行环境。请重试；如系统提示需要重启，请重启电脑。'
    } catch {
        $State.Message = "准备失败：$($_.Exception.Message)"
        Write-PreparationLog $DiagnosticPath ($State.Message + "`n" + $_.ScriptStackTrace)
        $State.Status = 'failed'
    } finally {
        # Do not remove files from an installer that is still running after timeout.
        if ($null -eq $State.Installer -or $State.Installer.HasExited) {
            Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Enter-PreparationMutex {
    param([Threading.Mutex]$Mutex)
    try { return $Mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { return $true }
}

if ($FunctionsOnly) { return }

# This WinForms UI runs before Tauri; it must never depend on WebView2.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$mutex = [Threading.Mutex]::new($false, "Local\zotero-pdf2zh-pro-webview2-$identity")
$owned = Enter-PreparationMutex $mutex
if (-not $owned) { $mutex.Dispose(); exit 10 }
$script:result = 20
$script:worker = $null
$script:operation = $null
$script:state = [hashtable]::Synchronized(@{ Status = 'working'; Message = '正在检查运行环境…'; Installer = $null })
$form = [Windows.Forms.Form]::new()
$form.Text = 'zotero-pdf2zh-pro · 首次准备'
$form.ClientSize = [Drawing.Size]::new(520, 230)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$label = [Windows.Forms.Label]::new()
$label.SetBounds(24, 22, 472, 90)
$label.Text = '正在准备运行环境，完成后将自动打开控制台。'
$progress = [Windows.Forms.ProgressBar]::new()
$progress.SetBounds(24, 118, 472, 16)
$progress.Style = 'Marquee'
$retry = [Windows.Forms.Button]::new()
$retry.Text = '重试'
$retry.SetBounds(24, 168, 90, 32)
$retry.Visible = $false
$manual = [Windows.Forms.Button]::new()
$manual.Text = '官方下载'
$manual.SetBounds(128, 168, 105, 32)
$manual.Visible = $false
$log = [Windows.Forms.Button]::new()
$log.Text = '查看日志'
$log.SetBounds(247, 168, 105, 32)
$log.Visible = $false
$form.Controls.AddRange(@($label, $progress, $retry, $manual, $log))
$timer = [Windows.Forms.Timer]::new()
$timer.Interval = 200
$sourcePath = $PSCommandPath

function Start-PreparationWorker {
    if ($null -ne $script:state.Installer -and $script:state.Installer.HasExited) {
        $script:state.Installer.Dispose()
        $script:state.Installer = $null
    }
    $script:state.Status = 'working'
    $script:state.Message = '正在检查运行环境…'
    $retry.Visible = $false
    $manual.Visible = $false
    $log.Visible = $false
    $progress.Visible = $true
    $script:worker = [PowerShell]::Create()
    [void]$script:worker.AddScript({
        param($Source, $ProgressState, $DiagnosticPath)
        try {
            . $Source -FunctionsOnly
            Invoke-WebView2Preparation -State $ProgressState -DiagnosticPath $DiagnosticPath
        } catch {
            $ProgressState.Message = "准备失败：$($_.Exception.Message)"
            $ProgressState.Status = 'failed'
        }
    }).AddArgument($sourcePath).AddArgument($script:state).AddArgument($LogPath)
    $script:operation = $script:worker.BeginInvoke()
}

$form.Add_Shown({ Start-PreparationWorker; $timer.Start() })
$retry.Add_Click({ Start-PreparationWorker })
$manual.Add_Click({ Start-Process 'https://developer.microsoft.com/microsoft-edge/webview2/' })
$log.Add_Click({
    if (Test-Path -LiteralPath $LogPath) {
        Start-Process notepad.exe -ArgumentList ('"' + $LogPath + '"')
    }
})
$form.Add_FormClosing({
    param($sender, $eventArgs)
    if ($script:state.Status -eq 'working') {
        $eventArgs.Cancel = $true
    }
})
$timer.Add_Tick({
    $label.Text = $script:state.Message
    if ($script:operation -and $script:operation.IsCompleted) {
        try { $script:worker.EndInvoke($script:operation) | Out-Null }
        catch {
            $script:state.Message = "准备失败：$($_.Exception.Message)"
            Write-PreparationLog $LogPath $script:state.Message
            $script:state.Status = 'failed'
        }
        $script:worker.Dispose()
        $script:worker = $null
        $script:operation = $null
        if ($script:state.Status -eq 'ready') {
            $script:result = 0
            $form.Close()
        }
    }
    if ($script:state.Status -eq 'failed') {
        $progress.Visible = $false
        $retry.Visible = $true
        $retry.Enabled = $null -eq $script:operation -and
            ($null -eq $script:state.Installer -or $script:state.Installer.HasExited)
        $manual.Visible = $true
        $log.Visible = $true
    }
})

try { [void]$form.ShowDialog() }
finally {
    $timer.Stop()
    $timer.Dispose()
    $form.Dispose()
    if ($script:worker) { $script:worker.Dispose() }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
exit $script:result

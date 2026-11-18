<#
.SYNOPSIS
    在 Windows 上把 askdata 跑起来（不依赖 Docker）。

.DESCRIPTION
    做四件事：检查环境 → 缺数据库就造 → 起服务 → 打印本机和局域网地址。
    服务默认监听 0.0.0.0，所以同一个局域网里的手机、平板、同事的电脑都能访问。

    注意：这个服务**没有鉴权**。只适合内网演示，不要直接暴露到公网。

.EXAMPLE
    pwsh scripts/deploy.ps1                 # 启动
    pwsh scripts/deploy.ps1 -Port 8080      # 换端口
    pwsh scripts/deploy.ps1 -Stop           # 停止
    pwsh scripts/deploy.ps1 -AutoStart      # 注册开机自启（计划任务）
#>
param(
    [int]$Port = 8777,
    [string]$BindHost = "0.0.0.0",
    [switch]$Stop,
    [switch]$AutoStart,
    [switch]$RemoveAutoStart
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pidFile = Join-Path $root ".server.pid"
$logFile = Join-Path $root "server.log"
$python = Join-Path $root ".venv\Scripts\python.exe"

function Stop-Server {
    if (Test-Path $pidFile) {
        $oldPid = (Get-Content $pidFile -ErrorAction SilentlyContinue) -as [int]
        if ($oldPid) {
            $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $oldPid -Force
                Write-Host "已停止服务（PID $oldPid）"
            }
        }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    } else {
        Write-Host "没有找到 PID 文件，尝试按端口停止"
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Host "已停止 PID $_" }
    }
}

if ($Stop) { Stop-Server; exit 0 }

if ($RemoveAutoStart) {
    Unregister-ScheduledTask -TaskName "askdata-server" -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已移除开机自启"
    exit 0
}

if ($AutoStart) {
    $action = New-ScheduledTaskAction -Execute "pwsh" `
        -Argument "-NoProfile -WindowStyle Hidden -File `"$PSScriptRoot\deploy.ps1`" -Port $Port"
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName "askdata-server" -Action $action -Trigger $trigger -Force | Out-Null
    Write-Host "已注册开机自启（计划任务 askdata-server）"
    exit 0
}

# ---------------- 环境检查 ----------------

Write-Host "==> 检查 Python 环境"
if (-not (Test-Path $python)) {
    Write-Host "找不到 .venv，先创建："
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}
$version = & $python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
Write-Host "    Python $version"

Write-Host "==> 检查依赖"
& $python -c "import duckdb, sqlglot" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "依赖缺失，正在安装……"
    & $python -m pip install -r requirements.txt
}

Write-Host "==> 检查数据库"
if (-not (Test-Path "data\askdata.duckdb")) {
    Write-Host "    数据库不存在，用固定种子现场生成（约 10 秒）"
    & $python scripts\build_db.py
} else {
    $mb = [math]::Round((Get-Item "data\askdata.duckdb").Length / 1mb, 1)
    Write-Host "    已存在（$mb MB）"
}

Write-Host "==> 检查密钥"
if (Test-Path ".env") {
    if (Select-String -Path ".env" -Pattern "DEEPSEEK_API_KEY=\S" -Quiet) {
        Write-Host "    已配置 DEEPSEEK_API_KEY，问数可用"
    } else {
        Write-Host "    .env 里没填 key —— 界面能看，但提问会失败"
    }
} else {
    Write-Host "    没有 .env —— 界面能看，但提问会失败（要的话 cp .env.example .env 再填）"
}

# ---------------- 启动 ----------------

Stop-Server | Out-Null

Write-Host "==> 启动服务（监听 ${BindHost}:$Port）"
$proc = Start-Process -FilePath $python `
    -ArgumentList @("-m", "askdata.server", "--host", $BindHost, "--port", $Port) `
    -RedirectStandardOutput $logFile -RedirectStandardError (Join-Path $root "server.err") `
    -WindowStyle Hidden -PassThru
Set-Content -Path $pidFile -Value $proc.Id

$ready = $false
for ($i = 1; $i -le 60; $i++) {
    try {
        $null = Invoke-WebRequest "http://127.0.0.1:$Port/api/overview" -UseBasicParsing -TimeoutSec 2
        $ready = $true
        break
    } catch { Start-Sleep -Milliseconds 500 }
}

if (-not $ready) {
    Write-Host "启动失败，日志尾部："
    Get-Content $logFile -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}

# python -m 会先起一个启动器进程，再 fork 出真正监听的服务进程，
# $proc.Id 可能不是最终那个。以端口上的实际 PID 为准，否则 -Stop 会停错进程。
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty OwningProcess
$serverPid = if ($listener) { $listener } else { $proc.Id }
Set-Content -Path $pidFile -Value $serverPid

Write-Host ""
Write-Host "服务已启动（PID $serverPid）"
Write-Host ""
Write-Host "  本机    http://127.0.0.1:$Port"
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress
foreach ($ip in $ips) {
    Write-Host "  局域网  http://${ip}:$Port"
}
Write-Host ""
Write-Host "  停止    pwsh scripts/deploy.ps1 -Stop"
Write-Host "  日志    $logFile"
Write-Host ""
Write-Host "提醒：服务没有鉴权，只用于内网演示，不要直接暴露到公网。"

# 容器端到端验证：Docker 动作在这里，HTTP 断言在 container_e2e.py。
#
# 为什么分开：这个环境里 Python 启动的子进程拿不到 Docker 的命名管道
# （docker info 直接 permission denied），而 PowerShell 可以。
#
# 用法：
#   pwsh scripts/container_e2e.ps1
#   pwsh scripts/container_e2e.ps1 -Image askdata:1.1.0 -Port 8778

param(
    [string]$Image = "askdata:1.1.0",
    [int]$Port = 8778,
    [string]$Name = "askdata-test",
    [string]$Volume = "askdata-data",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
if (-not (Test-Path $docker)) { $docker = "docker" }

function Wait-Ready {
    param([int]$Seconds = 120)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod "http://127.0.0.1:$Port/ready" -TimeoutSec 3
            if ($r.ok) { return $true }
        } catch { Start-Sleep -Seconds 2 }
    }
    return $false
}

Write-Host "== 1. 清理并用最新镜像启动 ==" -ForegroundColor Cyan
& $docker rm -f $Name 2>$null | Out-Null
& $docker volume rm $Volume 2>$null | Out-Null

$runArgs = @("run", "-d", "--name", $Name, "-p", "${Port}:8777")
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    $keyLine = Select-String -Path $envFile -Pattern '^DEEPSEEK_API_KEY=' | Select-Object -First 1
    if ($keyLine) {
        $key = ($keyLine.Line -split '=', 2)[1].Trim()
        $runArgs += @("-e", "DEEPSEEK_API_KEY=$key")
    }
}
$runArgs += @("-v", "${Volume}:/app/data", $Image)
& $docker @runArgs | Out-Null

Write-Host "  等待就绪（首次要建库）…" -ForegroundColor DarkGray
$sw = [Diagnostics.Stopwatch]::StartNew()
if (-not (Wait-Ready)) {
    Write-Host "  就绪失败，日志：" -ForegroundColor Red
    & $docker logs $Name --tail 25
    exit 1
}
Write-Host "  就绪 $([int]$sw.Elapsed.TotalSeconds)s" -ForegroundColor Green

Write-Host "== 2. 功能断言（HTTP）==" -ForegroundColor Cyan
& (Join-Path $root ".venv\Scripts\python.exe") scripts\container_e2e.py --phase before
$beforeCode = $LASTEXITCODE

Write-Host "== 3. 重启容器 ==" -ForegroundColor Cyan
& $docker restart $Name | Out-Null
if (-not (Wait-Ready)) {
    Write-Host "  重启后未就绪" -ForegroundColor Red
    & $docker logs $Name --tail 25
    exit 1
}
Write-Host "  已就绪" -ForegroundColor Green

Write-Host "== 4. 持久化断言（HTTP）==" -ForegroundColor Cyan
& (Join-Path $root ".venv\Scripts\python.exe") scripts\container_e2e.py --phase after
$afterCode = $LASTEXITCODE

Write-Host "== 5. 运行状态 ==" -ForegroundColor Cyan
& $docker ps --filter "name=$Name" --format "  状态 {{.Status}}"
& $docker images $Image --format "  体积 {{.Size}}"
$pipCheck = (& $docker run --rm $Image python -c "import importlib.util as u; print('已移除' if u.find_spec('pip') is None else '仍存在')" 2>$null)
Write-Host "  pip $pipCheck"
$userCheck = (& $docker run --rm $Image id -u 2>$null)
Write-Host "  uid $userCheck"

if (-not $Keep) {
    Write-Host "== 6. 清理 ==" -ForegroundColor Cyan
    & $docker rm -f $Name | Out-Null
    & $docker volume rm $Volume | Out-Null
    Write-Host "  已删除测试容器与卷"
}

if ($beforeCode -ne 0 -or $afterCode -ne 0) { exit 1 }
Write-Host "`n容器验证全部通过。" -ForegroundColor Green

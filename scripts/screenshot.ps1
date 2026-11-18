<#
.SYNOPSIS
    给 askdata 的六个页面批量截图，每个页面各出日间 / 夜间两版。

.DESCRIPTION
    用 Edge / Chrome 的无头模式渲染真实页面，不依赖任何 npm 包。

    两个关键参数，少一个截出来就不对：
      --force-prefers-reduced-motion
          页面有入场动画（面板上浮 + 淡入）。无头模式的时间推进和真实时钟不同步，
          不加这个会截到动画中间——整页发灰、字看不清。我踩过这个坑。
      --virtual-time-budget=10000
          页面要等 /api/* 返回后才渲染出内容，给够虚拟时间让它跑完。

    主题通过 ?theme=dark 传给前端（initTheme 支持这个参数），
    所以不需要操作 localStorage。

.EXAMPLE
    pwsh scripts/screenshot.ps1
    pwsh scripts/screenshot.ps1 -OutDir docs/shots -Height 1200
    pwsh scripts/screenshot.ps1 -Only ask,eval -Themes light
#>
param(
    [string]$Base = "http://127.0.0.1:8777",
    [string]$OutDir = "docs/shots",
    [int]$Width = 1600,
    [int]$Height = 1000,
    [string[]]$Themes = @("light", "dark"),
    [string[]]$Only = @(),
    [int]$BudgetMs = 10000
)

# 注意：PS 5.1 里原生命令写 stderr 会被当成 terminating error（Edge 总会打几条
# 无关紧要的 ERROR），所以这里用 Continue，真正的失败靠显式 throw。
$ErrorActionPreference = "Continue"

function Find-Browser {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    throw "找不到 Edge / Chrome。装一个，或者手动传 -Browser 路径。"
}

# 页面 key → URL 片段（hash 路由）
$pages = [ordered]@{
    "ask"     = ""
    "dict"    = "#dict"
    "eval"    = "#eval"
    "guard"   = "#guard"
    "clarify" = "#clarify"
    "about"    = "#about"
    "settings" = "#settings"
}

if ($Only.Count -gt 0) {
    $filtered = [ordered]@{}
    foreach ($key in $pages.Keys) {
        if ($Only -contains $key) { $filtered[$key] = $pages[$key] }
    }
    if ($filtered.Count -eq 0) { throw "没有匹配的页面：$($Only -join ', ')" }
    $pages = $filtered
}

$browser = Find-Browser
Write-Host "浏览器  $browser"
Write-Host "输出到  $OutDir"
Write-Host ""

# 先确认服务活着，否则会截出一屏"初始化失败"
try {
    $null = Invoke-WebRequest "$Base/api/overview" -UseBasicParsing -TimeoutSec 15
} catch {
    throw "服务没起来（$Base/api/overview 打不开）。先跑：.venv\Scripts\python.exe -m askdata.server --port 8777"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
# 无头浏览器不认相对输出路径，必须转成绝对路径
$OutDir = (Resolve-Path $OutDir).Path

$made = @()
$failed = @()

foreach ($theme in $Themes) {
    if ($theme -notin @("light", "dark")) { throw "主题只能是 light / dark，收到：$theme" }
    foreach ($name in $pages.Keys) {
        $url = "$Base/?theme=$theme$($pages[$name])"
        $file = Join-Path $OutDir "$name-$theme.png"
        if (Test-Path $file) { Remove-Item $file -Force }

        & $browser --headless=new --disable-gpu --hide-scrollbars `
            --window-size="$Width,$Height" `
            --screenshot="$file" `
            --virtual-time-budget=$BudgetMs `
            --force-prefers-reduced-motion `
            $url 2>$null | Out-Null

        if ((Test-Path $file) -and (Get-Item $file).Length -gt 10kb) {
            $kb = [math]::Round((Get-Item $file).Length / 1kb)
            Write-Host ("  [ ok ] {0,-16} {1,5} KB" -f "$name-$theme", $kb)
            $made += $file
        } else {
            Write-Host ("  [FAIL] {0,-16} 没有生成文件" -f "$name-$theme")
            $failed += "$name-$theme"
        }
    }
}

Write-Host ""
Write-Host ("生成 {0} 张，失败 {1} 张" -f $made.Count, $failed.Count)
if ($failed.Count -gt 0) {
    Write-Host ("失败：{0}" -f ($failed -join ", "))
    exit 1
}
Write-Host "提示：改完样式后重跑本脚本，用 git diff 看截图变化，比肉眼比对可靠。"
exit 0

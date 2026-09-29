# scripts/setup_venv_keys.ps1
# 将 API Key 写入虚拟环境的激活脚本，使每次 activate 自动注入环境变量。
# 密钥只保存在本机 venv 的激活脚本中，绝不进入 Git 仓库。
#
# 用法：
#   1. 先创建虚拟环境：  python -m venv .venv
#   2. 运行本脚本：      powershell -ExecutionPolicy Bypass -File scripts\setup_venv_keys.ps1
#   3. 启动应用：        双击 启动.bat（会自动激活 venv 并注入密钥）
#
# 可选参数：
#   -Venv 路径    指定虚拟环境目录（默认 .venv）
#   -Clean        清除已写入的密钥条目（不会删除 venv 本身）

param(
    [string]$Venv = ".venv",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$activatePs1 = Join-Path $root "$Venv\Scripts\Activate.ps1"
$activateBat = Join-Path $root "$Venv\Scripts\activate.bat"

if (-not (Test-Path $activatePs1) -and -not (Test-Path $activateBat)) {
    Write-Error "未找到虚拟环境 $Venv（期望 $activatePs1 / $activateBat）。请先执行：python -m venv $Venv"
    exit 1
}

$psMarker = "# >>> deepseek_client keys >>>"
$psEndMarker = "# <<< deepseek_client keys <<<"
# .bat 中 # 不是注释符，>>> 会被当作重定向，必须用 rem
$batMarker = "rem >>> deepseek_client keys >>>"
$batEndMarker = "rem <<< deepseek_client keys <<<"

# 需要写入的变量。SOURCE 为来源选择（非密钥），留空则使用代码默认值 opencode_go。
$vars = @(
    "SOURCE",
    "DEEPSEEK_API_KEY",
    "OPENCODE_GO_API_KEY",
    "ZBIGMODEL_API_KEY",
    "BAILIAN_TOKEN_PLAN_API_KEY",
    "STEPFUN_API_KEY",
    "XIAOMI_MIMO_API_KEY"
)

function Get-CurrentUserValue([string]$name) {
    $cur = [Environment]::GetEnvironmentVariable($name, "User")
    if ($cur) { $cur } else { "" }
}

$psLines = @()
$batLines = @()

if (-not $Clean) {
    Write-Host "以下变量会写入 $activatePs1 与 $activateBat。回车沿用当前用户级值或输入新值。"
    foreach ($v in $vars) {
        $existing = Get-CurrentUserValue $v
        $hint = if ($existing) { " (现有值: 回车沿用)" } else { "" }
        $val = Read-Host ("{0}{1}" -f $v, $hint)
        if (-not $val) { $val = $existing }
        if (-not $val) { continue }

        # 去除可能破坏脚本的引号/反引号，保持 ASCII
        $safe = $val -replace '["`'']', ''
        $psLines += ('$env:{0} = "{1}"' -f $v, $safe)
        # cmd 特殊字符转义：% ! ^ & | < >
        $batVal = $safe -replace '[%!^&|<>]', { '^' + $_.Value } 
        $batLines += ('set {0}={1}' -f $v, $batVal)
    }
}

function Update-File([string]$path, [string[]]$lines, [string]$openTag, [string]$closeTag) {
    if (-not (Test-Path $path)) { return }
    $content = [System.IO.File]::ReadAllText($path)
    $pattern = '(?s)' + [regex]::Escape($openTag) + '.*?' + [regex]::Escape($closeTag) + '\r?\n?'
    $content = [regex]::Replace($content, $pattern, '')

    if (-not $Clean -and $lines.Count -gt 0) {
        # 批处理/脚本要求 CRLF 行尾，混用 LF 会导致 cmd 解析出错
        $nl = "`r`n"
        $block = $openTag + $nl + ($lines -join $nl) + $nl + $closeTag + $nl
        $content = $content.TrimEnd("`r", "`n") + $nl + $block
    }

    # 注入内容为纯 ASCII，用 ASCII 编码写出（.bat 带 UTF-8 BOM 会导致 cmd 解析失败）
    [System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::ASCII)
    Write-Host "已更新 $path"
}

Update-File $activatePs1 $psLines $psMarker $psEndMarker
Update-File $activateBat $batLines $batMarker $batEndMarker

if ($Clean) {
    Write-Host "已清除密钥条目。"
} elseif ($psLines.Count -eq 0 -and $batLines.Count -eq 0) {
    Write-Host "未写入任何变量（全部留空）。"
} else {
    Write-Host "完成。双击 启动.bat 启动应用（会自动激活 venv 并注入密钥）。"
}
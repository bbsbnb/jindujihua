$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VirtualEnvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VirtualEnvPython) {
    $Python = $VirtualEnvPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
} else {
    throw "未找到 Python 3.11+。请安装 Python 后重试。"
}

Write-Host "进度计划 Agent: http://127.0.0.1:8765"
& $Python app.py --host 127.0.0.1 --port 8765

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Install-Package($Id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw '请安装 Microsoft App Installer，或按 docs/windows.md 手动安装 Python 3.12、Git 和 FFmpeg。' }
    & winget install --id $Id --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "安装 $Id 失败，退出码 $LASTEXITCODE" }
    Refresh-Path
}
$PythonAvailable = $false
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        & py -3.12 -c 'import sys; assert sys.maxsize > 2**32' 2>&1 | Out-Null
        $PythonAvailable = ($LASTEXITCODE -eq 0)
    } catch { $PythonAvailable = $false }
}
if (-not $PythonAvailable) { Install-Package 'Python.Python.3.12' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Install-Package 'Git.Git' }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Install-Package 'Gyan.FFmpeg' }
& py -3.12 scripts/install.py --engines
if ($LASTEXITCODE -ne 0) { throw "引擎安装失败，退出码 $LASTEXITCODE。请保留窗口日志。" }

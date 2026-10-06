$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}
function Install-Package($Id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw '请安装 Microsoft App Installer，或按 docs/windows.md 手动安装 Python 3.12、Git 和 FFmpeg。' }
    & winget install --id $Id --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "安装 $Id 失败，退出码 $LASTEXITCODE" }
    Refresh-Path
}
$InstallExitCode = 0
$TranscriptStarted = $false
$LogPath = $null
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
try {
    $LogDirectory = Join-Path (Get-Location).Path 'runtime\diagnostics'
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    $LogPath = Join-Path $LogDirectory ('install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.log')
    Start-Transcript -Path $LogPath -Force | Out-Null
    $TranscriptStarted = $true
    Write-Host "安装日志：$LogPath"
    Write-Host '[1/5] 检查 Python 3.12 x64...'
    $PythonAvailable = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            & py -3.12 -c 'import sys; assert sys.maxsize > 2**32' 2>&1 | Out-Null
            $PythonAvailable = ($LASTEXITCODE -eq 0)
        } catch { $PythonAvailable = $false }
    }
    if (-not $PythonAvailable) { Install-Package 'Python.Python.3.12' }
    Write-Host '[2/5] 检查 Git...'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Install-Package 'Git.Git' }
    Write-Host '[3/5] 检查 FFmpeg...'
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Install-Package 'Gyan.FFmpeg' }
    Write-Host '[4/5] 安装工作台与 GPU 引擎；首次下载可能需要较长时间...'
    & py -3.12 scripts/install.py --engines
    if ($LASTEXITCODE -ne 0) { throw "引擎安装失败，退出码 $LASTEXITCODE。请保留窗口日志。" }
    Write-Host '[5/5] 安装完成，可以运行 Start-Windows.cmd。'
} catch {
    $InstallExitCode = 1
    Write-Host ('安装失败：' + $_.Exception.Message) -ForegroundColor Red
    if ($LogPath) { Write-Host "请保留安装日志：$LogPath" }
} finally {
    if ($TranscriptStarted) { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null }
}
exit $InstallExitCode

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
. (Join-Path $PSScriptRoot 'windows-prerequisites.ps1')
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
    Refresh-Path
    $PythonExecutable = Find-Python312
    if (-not $PythonExecutable) {
        Install-Package 'Python.Python.3.12'
        $PythonExecutable = Find-Python312
    }
    if (-not $PythonExecutable) { throw '没有找到可运行的 Python 3.12 x64，请检查安装路径和 docs/windows.md。' }
    Write-Host "使用 Python：$PythonExecutable"
    Write-Host '[2/5] 检查 Git...'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Install-Package 'Git.Git' }
    Write-Host '[3/5] 检查 FFmpeg...'
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Install-Package 'Gyan.FFmpeg' }
    Write-Host '[4/5] 安装工作台与 GPU 引擎；首次下载可能需要较长时间...'
    & $PythonExecutable scripts/install.py --engines
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

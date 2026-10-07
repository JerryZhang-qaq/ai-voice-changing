param([string]$TargetDirectory, [switch]$Upgrade)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
. (Join-Path $PSScriptRoot 'windows-prerequisites.ps1')
$InstallExitCode = 0
$TranscriptStarted = $false
$LogPath = $null
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$PackageDirectory = Split-Path $PSScriptRoot -Parent
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
    if (-not $TargetDirectory) {
        if ($Upgrade -or ((Test-Path (Join-Path $PackageDirectory 'RELEASE-MANIFEST.json')) -and -not (Test-Path (Join-Path $PackageDirectory 'runtime\venvs')))) {
            Add-Type -AssemblyName System.Windows.Forms
            $Picker = New-Object System.Windows.Forms.FolderBrowserDialog
            $Picker.Description = '0.0.4 覆盖升级：选择正在使用的安装目录（例如 0.0.3）。保留全部素材、数据集、缓存、模型、基础权重与环境。首次安装可选择当前新包目录。请先关闭旧工作台。'
            $Picker.SelectedPath = $PackageDirectory
            $Picker.ShowNewFolderButton = $false
            if ($Picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw '已取消安装。' }
            $TargetDirectory = $Picker.SelectedPath
        } else { $TargetDirectory = $PackageDirectory }
    }
    if (Test-Path (Join-Path $PackageDirectory 'RELEASE-MANIFEST.json')) {
        & $PythonExecutable (Join-Path $PSScriptRoot 'upgrade.py') --target $TargetDirectory
        if ($LASTEXITCODE -ne 0) { throw '覆盖升级未完成，请保留窗口日志。' }
    } elseif ((Resolve-Path $TargetDirectory).Path -ne (Resolve-Path $PackageDirectory).Path) { throw '覆盖升级需要完整解压的 0.0.4 ZIP 安装包。' }
    Set-Location $TargetDirectory
    Write-Host "当前安装目录：$TargetDirectory"
    Write-Host '[2/5] 检查 Git...'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Install-Package 'Git.Git' }
    Write-Host '[3/5] 检查 FFmpeg...'
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Install-Package 'Gyan.FFmpeg' }
    Write-Host '[4/5] 安装工作台与 GPU 引擎；首次下载可能需要较长时间...'
    & $PythonExecutable scripts/install.py --engines
    if ($LASTEXITCODE -ne 0) { throw "引擎安装失败，退出码 $LASTEXITCODE。请保留窗口日志。" }
    Write-Host ('[5/5] 安装完成，请运行：' + (Join-Path $TargetDirectory 'Start-Windows.cmd'))
} catch {
    $InstallExitCode = 1
    Write-Host ('安装失败：' + $_.Exception.Message) -ForegroundColor Red
    if ($LogPath) { Write-Host "请保留安装日志：$LogPath" }
} finally {
    if ($TranscriptStarted) { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null }
}
exit $InstallExitCode

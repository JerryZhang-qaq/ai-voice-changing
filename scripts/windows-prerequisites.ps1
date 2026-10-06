function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}

function Find-Python312 {
    $Candidates = @()
    foreach ($Path in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python\Python312\python.exe')
    )) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $Candidates += [pscustomobject]@{ Command = $Path; Arguments = @() }
        }
    }
    foreach ($Name in @('py.exe', 'python.exe')) {
        $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Command -and $Command.Source -notlike '*\Microsoft\WindowsApps\*') {
            $Arguments = @()
            if ($Name -eq 'py.exe') { $Arguments = @('-3.12') }
            $Candidates += [pscustomobject]@{ Command = $Command.Source; Arguments = $Arguments }
        }
    }
    foreach ($Key in @('HKCU:\Software\Python\PythonCore\3.12\InstallPath', 'HKLM:\Software\Python\PythonCore\3.12\InstallPath')) {
        $RegistryKey = Get-Item -LiteralPath $Key -ErrorAction SilentlyContinue
        if ($RegistryKey) {
            $Directory = $RegistryKey.GetValue('')
            if ($Directory) {
                $Candidates += [pscustomobject]@{ Command = (Join-Path $Directory 'python.exe'); Arguments = @() }
            }
        }
    }
    foreach ($Candidate in $Candidates) {
        try {
            $ProbeArguments = @($Candidate.Arguments) + @('-c', 'import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32; print(sys.executable)')
            $Output = & $Candidate.Command @ProbeArguments 2>$null
            if ($LASTEXITCODE -eq 0) {
                $Resolved = [string]($Output | Select-Object -Last 1)
                if (Test-Path -LiteralPath $Resolved -PathType Leaf) { return $Resolved }
            }
        } catch {
            # A missing launcher or incompatible candidate does not hide the other installations.
        }
    }
    return $null
}

function Install-Package($Id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw '请安装 Microsoft App Installer，或按 docs/windows.md 手动安装 Python 3.12、Git 和 FFmpeg。' }
    & winget install --id $Id --exact --source winget --accept-package-agreements --accept-source-agreements
    $PackageExitCode = $LASTEXITCODE
    Refresh-Path
    if ($PackageExitCode -eq -1978335189) {
        # APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE: verify the existing tool below.
        Write-Host "$Id 已安装且没有可升级版本，继续检查现有工具。"
        return
    }
    if ($PackageExitCode -ne 0) { throw "安装 $Id 失败，退出码 $PackageExitCode" }
}

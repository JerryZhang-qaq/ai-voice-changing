import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows PowerShell")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(script, env=None):
    source = quote(ROOT / "scripts/windows-prerequisites.ps1")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "$ErrorActionPreference = 'Stop'; . " + source + "; " + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=45, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_existing_python_without_launcher_or_path(tmp_path):
    local = tmp_path / "local app data"
    directory = local / "Programs/Python/Python312"
    directory.mkdir(parents=True)
    executable = directory / "python.exe"
    shutil.copy2(sys.executable, executable)
    for pattern in ("python*.dll", "vcruntime*.dll"):
        for dll in Path(sys.base_prefix).glob(pattern):
            shutil.copy2(dll, directory / dll.name)
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local)
    env["PYTHONHOME"] = sys.base_prefix
    output = run_ps(
        "function Get-Command { return $null }; "
        "$resolved = Find-Python312; "
        "if (-not $resolved) { throw 'Existing Python not detected' }; "
        "$resolved | ConvertTo-Json -Compress", env=env,
    )
    assert Path(json.loads(output.strip())) == executable


def test_winget_no_upgrade_is_not_an_install_failure():
    output = run_ps(
        "function winget { $global:LASTEXITCODE = -1978335189 }; "
        "Install-Package 'Python.Python.3.12'; Write-Output 'NO_UPGRADE_HANDLED'",
    )
    assert "NO_UPGRADE_HANDLED" in output


def test_other_winget_failure_remains_a_failure():
    output = run_ps(
        "function winget { $global:LASTEXITCODE = 17 }; "
        "try { Install-Package 'Python.Python.3.12' } "
        "catch { if ($_.Exception.Message -match '17') { Write-Output 'FAILURE_RETAINED'; exit 0 }; throw }; "
        "throw 'Unexpected winget error was ignored'",
    )
    assert "FAILURE_RETAINED" in output

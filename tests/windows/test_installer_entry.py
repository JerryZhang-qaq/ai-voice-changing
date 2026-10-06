"""Exercise real CMD/PowerShell, without installing any packages."""
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows CMD")


def prepare(tmp_path, name="Install-Windows.cmd"):
    directory = tmp_path / "入口 空格 & !test"
    directory.mkdir()
    (directory / name).write_bytes((ROOT / name).read_bytes())
    return directory


def invoke(directory, name):
    return subprocess.run(
        ["cmd.exe", "/d", "/c", name], cwd=directory, input="\n",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", timeout=45,
    )


def test_failure_keeps_window_open_and_preserves_exit_code(tmp_path):
    directory = prepare(tmp_path)
    scripts = directory / "scripts"
    scripts.mkdir()
    (scripts / "install-windows.ps1").write_text(
        "Write-Host 'INSTALLER_STUB_REACHED'\nexit 37\n", encoding="utf-8-sig",
    )
    # Reproduce the exact old LF/UTF-8 entry from release 0.0.1.
    legacy = (
        '@echo off\nchcp 65001 >nul\ncd /d "%~dp0"\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\\install-windows.ps1\n'
        'if errorlevel 1 echo 安装失败，请查看上面的错误及 docs\\windows.md。\npause\n'
    )
    (directory / "Legacy.cmd").write_bytes(legacy.encode("utf-8"))
    old = invoke(directory, "Legacy.cmd")
    print("Legacy 0.0.1 CMD output:\n" + old.stdout)
    process = subprocess.Popen(
        ["cmd.exe", "/d", "/c", "Install-Windows.cmd"], cwd=directory,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    lines = queue.Queue()
    reader = threading.Thread(
        target=lambda: [lines.put(line) for line in process.stdout], daemon=True,
    )
    reader.start()
    output = ""
    deadline = time.monotonic() + 45
    try:
        while "Press any key to close this window." not in output:
            output += lines.get(timeout=max(0.01, deadline - time.monotonic()))
        assert "INSTALLER_STUB_REACHED" in output
        assert "Exit code: 37" in output
        time.sleep(0.2)
        assert process.poll() is None, "Installer window closed before user input"
        process.stdin.write("\n")
        process.stdin.flush()
        assert process.wait(timeout=15) == 37
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        reader.join(timeout=5)
        process.stdin.close()
        process.stdout.close()


def test_partial_extraction_is_reported(tmp_path):
    result = invoke(prepare(tmp_path), "Install-Windows.cmd")
    assert result.returncode == 1
    assert "Extract the entire ZIP" in result.stdout
    assert "Press any key" in result.stdout


@pytest.mark.parametrize("name", [
    "Start-Windows.cmd", "Diagnose-Windows.cmd", "GPU-Test-Windows.cmd",
    "Prepare-Test-Datasets-Windows.cmd",
])
def test_other_entries_report_missing_environment(tmp_path, name):
    result = invoke(prepare(tmp_path, name), name)
    assert result.returncode == 1
    assert "Run Install-Windows.cmd first" in result.stdout
    assert "Press any key" in result.stdout


def test_powershell_bootstrap_syntax():
    source = str(ROOT / "scripts/install-windows.ps1").replace("'", "''")
    command = (
        "$tokens = $null; $errors = $null; "
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{source}', [ref]$tokens, [ref]$errors); "
        "if ($errors.Count) { $errors | Out-String | Write-Output; exit 1 }; exit 0"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr

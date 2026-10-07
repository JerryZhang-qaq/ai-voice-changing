"""Exercise dependency checks with the real interpreter; never run pip in tests."""
import importlib.metadata
import importlib.util
from pathlib import Path
import sys


def test_installer_reuses_matching_dependencies_and_repairs_missing_pins(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/install.py'
    spec = importlib.util.spec_from_file_location('workbench_installer_reuse_test', path)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    calls = []
    monkeypatch.setattr(installer, 'run', lambda args, **kwargs: calls.append((args, kwargs)))
    installer.ensure_packages(sys.executable, ['pytest'], {'pytest': importlib.metadata.version('pytest')})
    assert calls == [], 'Matching environments must not invoke network resolution'
    environment = {'CC': 'gcc'}
    installer.ensure_packages(sys.executable, ['pytest==0.0.0'], {'pytest': '0.0.0'}, env=environment)
    assert calls == [([sys.executable, '-m', 'pip', 'install', 'pytest==0.0.0'], {'env': environment})]
    installer.ensure_packages(sys.executable, ['workbench-missing-test-dependency'], {'workbench-missing-test-dependency': '1'})
    assert len(calls) == 2

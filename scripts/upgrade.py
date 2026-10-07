"""Verified in-place 0.0.3 upgrade. Environments and trained outputs stay in place."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/storage/src"))
from voice_workbench_storage import ArtifactStore
from voice_workbench_storage.locking import WorkerLock
from voice_workbench_storage.store import is_reference

VERSION = "0.0.3"
ROOT_FILES = {"README.md", "LICENSE", "THIRD_PARTY.md", "pyproject.toml", "requirements.lock", ".env.example", ".gitignore", ".gitattributes", ".dockerignore"}
CODE_DIRECTORIES = {"packages", "services", "scripts", "tests", "docs", "deploy", ".github", "apps"}
REQUIRED_FILES = {"pyproject.toml", "apps/web/dist/index.html", "Install-Windows.cmd", "Start-Windows.cmd",
                  "scripts/install.py", "scripts/upgrade.py", "packages/storage/src/voice_workbench_storage/store.py",
                  "services/api/src/voice_workbench_api/app.py", "services/worker/src/voice_workbench_worker/runner.py"}


def allowed(relative):
    parts = Path(relative).parts
    return (relative.replace("\\", "/") == relative and parts and not Path(relative).is_absolute()
            and not any(p in {".", "..", ".git", "runtime", ".venv", "node_modules", "__pycache__"} for p in parts)
            and (parts[0] in CODE_DIRECTORIES or relative in ROOT_FILES or len(parts) == 1 and relative.endswith("-Windows.cmd")))


def checked_manifest(source):
    manifest = json.loads((source / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not REQUIRED_FILES.issubset(manifest):
        raise ValueError("安装包文件清单不完整，请重新完整解压 0.0.3 ZIP")
    for relative, expected in manifest.items():
        if not allowed(relative):
            raise ValueError(f"安装包包含越界文件路径：{relative}")
        path = source / relative
        if any(is_reference(p) for p in (path, *path.parents) if p == source or source in p.parents):
            raise ValueError("安装包不能包含目录引用")
        data = path.read_bytes()
        if len(data) != expected["size"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError(f"安装包完整性校验失败：{relative}；请重新完整解压")
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["version"] != VERSION or project["name"] != "voice-workbench":
        raise ValueError("此升级器仅适用于 0.0.3 安装包")
    return manifest


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clear_legacy(runtime, cutoff):
    store = ArtifactStore(runtime)
    # WorkerLock proves the owning worker is stopped. Stale leases from crashed
    # workers are released explicitly before the user's requested old-data reset.
    for job in store.jobs():
        if job["status"] in {"queued", "running"}:
            store.update_job(job["id"], "interrupted", error="0.0.3 覆盖升级已终止旧任务，请在新版本重新提交")
    selected = []
    for item in store.inventory()["items"]:
        if item["created_at"] > cutoff:
            continue
        kind = item["metadata"].get("kind")
        if (item["role"] in {"source", "dataset"} or kind in {"dataset_export", "dataset_prepared_export"}
                or item["role"] == "cache" and kind not in {"workspace", "converted_audio"}):
            selected.append(item["id"])
    print(f"清理旧音源、切片与数据集版本：{len(selected)} 项；保留模型、索引、转换结果、成品及训练工作目录。", flush=True)
    if not selected:
        return {"deleted_ids": [], "reclaimed_bytes": 0, "errors": [], "skipped": []}
    return store.delete_artifacts(selected)


def upgrade(source, target):
    source = Path(source).absolute()
    target = Path(target).absolute()
    if any(is_reference(p) for p in (target, *target.parents)):
        raise ValueError("原安装目录不能是符号链接或目录联接")
    manifest = checked_manifest(source)
    if source != target:
        project = tomllib.loads((target / "pyproject.toml").read_text(encoding="utf-8"))
        if project["project"]["name"] != "voice-workbench":
            raise ValueError("请选择原 VoiceWorkbench 安装文件夹")
    runtime = target / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    receipt = runtime / "diagnostics/upgrade-0.0.3.json"
    pending = runtime / "diagnostics/upgrade-0.0.3-pending.json"
    try:
        lock = WorkerLock(runtime / "worker.lock")
        lock.__enter__()
    except RuntimeError as error:
        raise RuntimeError("请先关闭旧工作台的启动窗口，再运行覆盖升级；旧文件尚未修改") from error
    try:
        catalog_exists = (runtime / "catalog.sqlite3").exists()
        app_version = None
        if catalog_exists:
            with sqlite3.connect(runtime / "catalog.sqlite3") as db:
                try:
                    row = db.execute("SELECT value FROM runtime_state WHERE key='app_version'").fetchone()
                    app_version = json.loads(row[0]).get("version") if row else None
                except sqlite3.OperationalError:
                    pass
        old_version = tomllib.loads((target / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        cleanup_needed = not receipt.exists() and catalog_exists and (pending.exists() or old_version != VERSION or app_version != VERSION)
        if cleanup_needed and not pending.exists():
            atomic_json(pending, {"version": VERSION, "cutoff": time.time()})
            with sqlite3.connect(runtime / "catalog.sqlite3") as original, sqlite3.connect(runtime / "diagnostics/catalog-before-0.0.3.sqlite3") as backup:
                original.backup(backup)
        old_manifest = {}
        if source != target and (target / "RELEASE-MANIFEST.json").is_file():
            old_manifest = json.loads((target / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        if source != target:
            for relative in manifest:
                destination = target / relative
                if any(is_reference(p) for p in (destination, *destination.parents) if p == target or target in p.parents):
                    raise ValueError(f"原代码路径包含目录引用：{relative}")
            for relative in manifest:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    with (source / relative).open("rb") as original:
                        shutil.copyfileobj(original, stream)
                try:
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            for obsolete in old_manifest.keys() - manifest.keys():
                if allowed(obsolete):
                    path = target / obsolete
                    if path.is_file() and not any(is_reference(p) for p in (path, *path.parents) if p == target or target in p.parents):
                        path.unlink()
            shutil.copyfile(source / "RELEASE-MANIFEST.json", target / "RELEASE-MANIFEST.json")
        if cleanup_needed:
            intent = json.loads(pending.read_text(encoding="utf-8"))
            result = clear_legacy(runtime, intent["cutoff"])
            if result["errors"] or result["skipped"]:
                raise RuntimeError(f"部分旧文件尚未清理，请关闭占用文件的软件后重新运行安装：{result}")
            atomic_json(receipt, {"version": VERSION, "target": str(target), "completed_at": time.time(), **result})
            pending.unlink(missing_ok=True)
        elif not receipt.exists():
            atomic_json(receipt, {"version": VERSION, "target": str(target), "completed_at": time.time(), "deleted_ids": [], "fresh_install": True})
        print(f"0.0.3 已覆盖到原目录：{target}。依赖环境和基础模型继续复用。", flush=True)
        return target
    finally:
        lock.__exit__(None, None, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    try:
        upgrade(ROOT, args.target)
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error))

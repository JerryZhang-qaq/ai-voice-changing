"""Pinned downloads; incomplete files belong to a cleanable job workspace."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tempfile
from urllib.request import Request, urlopen
from urllib.error import URLError

from .registry import settings
from .process import EngineError


def catalog():
    return json.loads(Path(__file__).with_name("resources.json").read_text(encoding="utf-8"))["resources"]


def selected(ids):
    specs = {item["id"]: item for item in catalog()}
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= specs.keys():
        raise ValueError("请选择不重复的有效资源")
    return [specs[rid] for rid in ids]


def destination(spec, entry):
    root = Path(settings()[spec["root"]]).resolve()
    path = root / entry["path"]
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise EngineError("资源路径包含外部链接")
    return path


def receipt_path(path):
    return path.with_name(path.name + ".workbench-sha256.json")


def verified(path, entry):
    try:
        stat = path.stat()
        receipt = json.loads(receipt_path(path).read_text(encoding="utf-8"))
        return (not path.is_symlink() and stat.st_size == entry["size"] and
                receipt == {"sha256": entry["sha256"], "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    except (OSError, ValueError):
        return False


def hash_file(path, progress=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            if progress:
                progress()
    return digest.hexdigest()


def save_receipt(path, entry):
    stat = path.stat()
    value = {"sha256": entry["sha256"], "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    target = receipt_path(path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        json.dump(value, stream)
    try:
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def inventory():
    result = []
    for spec in catalog():
        files = []
        for entry in spec["files"]:
            path = destination(spec, entry)
            files.append({**entry, "present": path.is_file(), "verified": verified(path, entry)})
        result.append({**spec, "files": files, "size": sum(f["size"] for f in files),
                       "ready": all(f["verified"] for f in files)})
    return result


def recover_install_partials(store):
    """Recover our cross-filesystem install staging after a killed worker."""
    parents = {destination(spec, entry).parent for spec in catalog() for entry in spec["files"]}
    for parent in parents:
        if not parent.is_dir():
            continue
        for path in parent.glob(".workbench-install-*.part"):
            match = re.fullmatch(r"\.workbench-install-([0-9a-f]{32})-.+\.part", path.name)
            if not match or path.is_symlink() or not path.is_file():
                continue
            with store.connect() as db:
                job = db.execute("SELECT kind,status FROM jobs WHERE id=?", (match[1],)).fetchone()
            if job and job["kind"] == "resources" and job["status"] not in {"queued", "running"}:
                path.unlink()


def install_resources(store, job_id, resource_ids, *, verify_only=False, progress=None):
    specs = selected(resource_ids)
    total = sum(entry["size"] for spec in specs for entry in spec["files"])
    done = 0
    workspace = store.allocate_workspace(job_id, name="资源下载临时文件", metadata={"resource_ids": resource_ids})
    work = store.path(workspace["id"])
    store.update_job(job_id, "running", metadata={"workspace_id": workspace["id"]})
    records = []
    for spec in specs:
        for entry in spec["files"]:
            path = destination(spec, entry)
            def tick(stage, offset=0):
                if progress:
                    progress(f"{stage} · {spec['name']} · {Path(entry['path']).name}", done + offset, total)
            tick("检查资源")
            if verified(path, entry):
                done += entry["size"]
                records.append({"path": entry["path"], "status": "verified"})
                continue
            if path.is_file():
                if path.stat().st_size != entry["size"] or hash_file(path, lambda: tick("校验已有文件")) != entry["sha256"]:
                    raise EngineError(f"已有资源校验失败：{path}。请移走该文件后重新下载；原文件未修改。")
                save_receipt(path, entry)
            elif verify_only:
                raise EngineError(f"资源缺失：{entry['path']}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(work).free < entry["size"] + 128 * 1024**2 or shutil.disk_usage(path.parent).free < entry["size"] + 128 * 1024**2:
                    raise EngineError("磁盘空间不足，需要容纳当前权重及下载临时文件")
                part = work / (entry["sha256"] + ".part")
                digest, received = hashlib.sha256(), 0
                try:
                    request = Request(entry["url"], headers={"User-Agent": "VoiceWorkbench/0.0.2"})
                    with urlopen(request, timeout=30) as response, part.open("wb") as out:
                        while block := response.read(1024 * 1024):
                            received += len(block)
                            if received > entry["size"]:
                                raise EngineError("资源下载大小超出固定清单")
                            out.write(block)
                            digest.update(block)
                            tick("下载资源", received)
                except (URLError, TimeoutError, OSError) as error:
                    raise EngineError(f"资源下载失败，临时文件可在缓存页清理：{error}") from error
                if received != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise EngineError("资源下载完整性校验失败；没有安装该文件")
                tick("安装已校验资源", received)
                if work.stat().st_dev == path.parent.stat().st_dev:
                    os.replace(part, path)
                    save_receipt(path, entry)
                    done += entry["size"]
                    records.append({"path": entry["path"], "status": "verified"})
                    continue
                # Different disks need a destination-local atomic rename. Its
                # owned temporary is recovered at the next worker startup.
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".workbench-install-{job_id}-", suffix=".part", delete=False) as out:
                    installed = Path(out.name)
                    try:
                        with part.open("rb") as source:
                            while block := source.read(1024 * 1024):
                                out.write(block)
                                tick("安装已校验资源", received)
                    except BaseException:
                        out.close()
                        installed.unlink(missing_ok=True)
                        raise
                try:
                    os.replace(installed, path)
                    save_receipt(path, entry)
                finally:
                    installed.unlink(missing_ok=True)
                part.unlink()
            done += entry["size"]
            records.append({"path": entry["path"], "status": "verified"})
    from io import BytesIO
    return store.import_stream(BytesIO(json.dumps({"resources": resource_ids, "files": records}, ensure_ascii=False).encode()),
                               name="资源校验记录.json", job_id=job_id, metadata={"kind": "resource_receipt"})["id"]

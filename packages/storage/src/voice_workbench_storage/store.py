"""SQLite catalog with explicit retention and serialized cache deletion.

Only managed files and workspaces can be deleted. Source/model/dataset/export roles are never
cache candidates. Running jobs hold direct inputs, and outputs are held on commit.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from typing import BinaryIO, Iterable
import uuid


class Conflict(ValueError):
    pass


class NotFound(ValueError):
    pass


ROLES = {"source", "cache", "dataset", "model", "export"}
ACTIVE = {"queued", "running"}


def singer_name(value):
    value = str(value).strip()
    if (not value or len(value) > 80 or value in {".", ".."} or
            re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((".", " ")) or
            value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *{f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}}):
        raise ValueError("歌手名字不能为空，且不能包含 Windows 文件夹名中的非法字符")
    return value


def readable_stem(value, limit=64):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(value)).strip(" .")[:limit]
    return value or "音频"


def is_reference(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


class ArtifactStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.files = self.root / "artifacts"
        self.files.mkdir(exist_ok=True)
        if is_reference(self.files):
            raise Conflict("产物目录不能是符号链接")
        self.db = self.root / "catalog.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, role TEXT NOT NULL, job_id TEXT,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL, retained INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'ready', metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    metadata TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS holds (
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    PRIMARY KEY(job_id, artifact_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL
                );
            """)
        self.recover_deletions()

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _path(self, relative: str) -> Path:
        parts = relative.split("/")
        valid = bool(re.fullmatch(r"(?:[^<>:\"/\\|?*\x00-\x1f]{1,80}__)?[0-9a-f]{32}(?:\.[A-Za-z0-9]{1,12})?", parts[-1]))
        if len(parts) > 1:
            valid = valid and len(parts) in {4, 5} and parts[0] == "歌手" and parts[2] in {"原始素材", "转换素材", "切片数据集", "处理中间文件"}
            if valid:
                valid = singer_name(parts[1]) == parts[1] and (len(parts) == 4 or bool(re.fullmatch(r"(?:[^<>:\"/\\|?*\x00-\x1f]{1,80}__)?[0-9a-f]{32}", parts[3])))
        if not valid:
            raise Conflict("无效的产物路径")
        path = self.files / relative
        if any(is_reference(p) for p in (path, *path.parents) if p == self.files or self.files in p.parents):
            raise Conflict("拒绝访问符号链接产物")
        if not path.resolve().is_relative_to(self.files.resolve()):
            raise Conflict("产物路径越界")
        return path

    def _placement(self, relative, role, metadata, job_id, name=None):
        metadata = dict(metadata or {})
        parent_id = metadata.get("source_id") or metadata.get("parent_id") or metadata.get("dataset_id")
        inherited = {}
        if parent_id:
            try:
                inherited = self.get(parent_id)["metadata"]
            except NotFound:
                pass
        if job_id:
            inherited = {**inherited, **self.job(job_id)["metadata"]}
        for key in ("singer", "purpose", "folder_name"):
            if key not in metadata and inherited.get(key):
                metadata[key] = inherited[key]
        if role == "source":
            metadata.setdefault("singer", "未分类歌手")
            metadata.setdefault("purpose", "training")
        if name:
            relative = readable_stem(Path(name).stem, 48) + "__" + relative
        if metadata.get("singer") and role not in {"model", "export"}:
            name = singer_name(metadata["singer"])
            category = "转换素材" if metadata.get("purpose") == "conversion" and role == "source" else "原始素材" if role == "source" else "切片数据集" if metadata.get("interval") or metadata.get("kind") == "dataset_manifest" else "处理中间文件"
            version = metadata.get("version_id") or job_id or metadata.get("parent_id")
            if version and not re.fullmatch(r"[0-9a-f]{32}", version):
                version = None
            if version and category == "切片数据集":
                label = "ready" if metadata.get("kind") == "dataset_manifest" and metadata.get("parent_id") else "after"
                group = Path(name).stem if name and metadata.get("kind") == "dataset_manifest" else f"{metadata['singer']}-{label}"
                version = readable_stem(group, 48) + "__" + version
            relative = "/".join(["歌手", name, category, *([version] if version and role != "source" else []), relative])
        return relative, metadata

    @staticmethod
    def _artifact(row):
        d = dict(row)
        d["metadata"] = json.loads(d["metadata"])
        d["retained"] = bool(d["retained"])
        d["location"] = d.pop("path", None)
        return d

    def import_file(self, source: Path | str, **kwargs):
        with Path(source).open("rb") as stream:
            return self.import_stream(stream, **kwargs)

    def import_stream(self, stream: BinaryIO, *, name: str, role="cache",
                      job_id=None, metadata=None, max_bytes=1024**3):
        if role not in ROLES:
            raise ValueError("未知产物类型")
        artifact_id = uuid.uuid4().hex
        suffix = Path(name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
            suffix = ""
        relative, metadata = self._placement(artifact_id + suffix, role, metadata, job_id, name)
        target = self._path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix="staging-", delete=False) as out:
                temp = Path(out.name)
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("文件超过上传大小限制")
                    out.write(chunk)
                    digest.update(chunk)
                out.flush()
                os.fsync(out.fileno())
            with self.connect(write=True) as db:
                if job_id and not db.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (job_id,)).fetchone():
                    raise Conflict("任务已结束，不能写入产物")
                os.replace(temp, target)
                db.execute("INSERT INTO artifacts (id,path,name,role,job_id,size,sha256,created_at,metadata) VALUES (?,?,?,?,?,?,?,?,?)",
                           (artifact_id, relative, Path(name).name, role, job_id, size, digest.hexdigest(), time.time(), json.dumps(metadata or {}, ensure_ascii=False)))
                if job_id:
                    db.execute("INSERT INTO holds VALUES (?,?)", (job_id, artifact_id))
        except BaseException:
            if temp:
                temp.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return self.get(artifact_id)

    def get(self, artifact_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=? AND state='ready'", (artifact_id,)).fetchone()
            if not row:
                raise NotFound("产物不存在或已清理")
            return self._artifact(row)

    def allocate_workspace(self, job_id, *, name, metadata=None):
        aid = uuid.uuid4().hex
        relative = aid + ".dir"
        relative, metadata = self._placement(relative, "cache", metadata, job_id, name)
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.connect(write=True) as db:
                if not db.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (job_id,)).fetchone():
                    raise Conflict("任务已结束")
                path.mkdir()
                details = {**(metadata or {}), "kind": "workspace"}
                db.execute("INSERT INTO artifacts (id,path,name,role,job_id,size,sha256,created_at,metadata) VALUES (?,?,?,?,?,?,?,?,?)",
                           (aid, relative, name, "cache", job_id, 0, "", time.time(), json.dumps(details, ensure_ascii=False)))
                db.execute("INSERT INTO holds VALUES (?,?)", (job_id, aid))
        except BaseException:
            if path.is_dir():
                path.rmdir()
            raise
        return self.get(aid)

    def path(self, artifact_id: str):
        with self.connect() as db:
            row = db.execute("SELECT path FROM artifacts WHERE id=? AND state='ready'", (artifact_id,)).fetchone()
            if not row:
                raise NotFound("产物不存在或已清理")
            path = self._path(row["path"])
            if not path.is_file() and not path.is_dir():
                raise NotFound("产物文件缺失")
            return path

    def retain(self, artifact_id, retained):
        with self.connect(write=True) as db:
            if not db.execute("UPDATE artifacts SET retained=? WHERE id=? AND state='ready'", (bool(retained), artifact_id)).rowcount:
                raise NotFound("产物不存在")
        return self.get(artifact_id)

    def promote_dataset(self, artifact_ids):
        """Accepted training audio is a permanent artifact, not a user-toggleable cache."""
        with self.connect(write=True) as db:
            for aid in artifact_ids:
                row = db.execute("SELECT path FROM artifacts WHERE id=? AND state='ready'", (aid,)).fetchone()
                if not row or not self._path(row["path"]).exists():
                    raise NotFound("片段已清理，请重新准备数据集")
                db.execute("UPDATE artifacts SET role='dataset' WHERE id=? AND role='cache'", (aid,))

    def create_job(self, kind, inputs=(), metadata=None, *, status="queued"):
        if status not in ACTIVE:
            raise ValueError("初始任务状态应为 queued 或 running")
        job_id, now = uuid.uuid4().hex, time.time()
        with self.connect(write=True) as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,NULL)", (job_id, kind, status, now, now, json.dumps(metadata or {}, ensure_ascii=False)))
            for aid in set(inputs):
                row = db.execute("SELECT path FROM artifacts WHERE id=? AND state='ready'", (aid,)).fetchone()
                if not row or not self._path(row["path"]).exists():
                    raise NotFound("任务输入已清理或缺失")
                db.execute("INSERT INTO holds VALUES (?,?)", (job_id, aid))
        return self.job(job_id)

    def hold_inputs(self, job_id, artifact_ids):
        with self.connect(write=True) as db:
            if not db.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (job_id,)).fetchone():
                raise Conflict("任务已结束")
            for aid in set(artifact_ids):
                row = db.execute("SELECT path FROM artifacts WHERE id=? AND state='ready'", (aid,)).fetchone()
                if not row or not self._path(row["path"]).exists():
                    raise NotFound("缓存已清理或文件缺失")
                db.execute("INSERT OR IGNORE INTO holds VALUES (?,?)", (job_id, aid))

    def cached(self, cache_key, job_id):
        """Acquire a lease before reading cached bytes; check the stored SHA-256."""
        with self.connect(write=True) as db:
            if not db.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (job_id,)).fetchone():
                raise Conflict("任务已结束")
            rows = db.execute("SELECT * FROM artifacts WHERE state='ready' AND json_extract(metadata,'$.cache_key')=? ORDER BY created_at DESC", (cache_key,)).fetchall()
            for row in rows:
                path = self._path(row["path"])
                if not path.is_file():
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != row["sha256"]:
                    metadata = json.loads(row["metadata"])
                    metadata.pop("cache_key", None)
                    metadata["integrity_error"] = True
                    db.execute("UPDATE artifacts SET metadata=? WHERE id=?", (json.dumps(metadata), row["id"]))
                    continue
                db.execute("INSERT OR IGNORE INTO holds VALUES (?,?)", (job_id, row["id"]))
                return self._artifact(row)
        return None

    def update_job(self, job_id, status, *, metadata=None, error=None):
        if status not in {"queued", "running", "completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("未知任务状态")
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise NotFound("任务不存在")
            if row["status"] not in ACTIVE:
                raise Conflict("已结束任务不能重新激活，请创建新任务")
            merged = json.loads(row["metadata"])
            merged.update(metadata or {})
            db.execute("UPDATE jobs SET status=?,updated_at=?,metadata=?,error=? WHERE id=?", (status, time.time(), json.dumps(merged, ensure_ascii=False), error, job_id))
            if status not in ACTIVE:
                db.execute("DELETE FROM holds WHERE job_id=?", (job_id,))
        return self.job(job_id)

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise NotFound("任务不存在")
            d = dict(row)
            d["metadata"] = json.loads(d["metadata"])
            return d

    def jobs(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 200")]
        return [self.job(i) for i in ids]

    def set_runtime(self, key, value):
        with self.connect(write=True) as db:
            db.execute("INSERT INTO runtime_state VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                       (key, json.dumps(value, ensure_ascii=False), time.time()))

    def touch_runtime(self, key):
        with self.connect(write=True) as db:
            db.execute("UPDATE runtime_state SET updated_at=? WHERE key=?", (time.time(), key))

    def runtime(self, key, max_age=30):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runtime_state WHERE key=?", (key,)).fetchone()
        if not row or time.time() - row["updated_at"] > max_age:
            return None
        return json.loads(row["value"])

    def _inventory(self, db):
        held = {r[0] for r in db.execute("SELECT DISTINCT artifact_id FROM holds JOIN jobs ON jobs.id=holds.job_id WHERE jobs.status IN ('queued','running')")}
        result = []
        for row in db.execute("SELECT * FROM artifacts WHERE state='ready' ORDER BY created_at DESC"):
            d = self._artifact(row)
            reasons = []
            if d["role"] != "cache":
                reasons.append("permanent")
            if d["retained"]:
                reasons.append("retained")
            if d["id"] in held:
                reasons.append("active_job")
            try:
                path = self._path(row["path"])
                d["exists"] = path.exists()
                if path.is_dir():
                    d["size"] = self._directory_bytes(path)
                elif path.is_file():
                    d["size"] = path.stat().st_size
            except Conflict:
                d["exists"] = False
                reasons.append("unsafe_path")
            d.update(cleanable=not reasons, protection=reasons,
                     manual_cleanable=d["role"] == "cache" and "active_job" not in reasons and "unsafe_path" not in reasons,
                     deletable="active_job" not in reasons and "unsafe_path" not in reasons)
            result.append(d)
        return result

    def inventory(self):
        with self.connect() as db:
            items = self._inventory(db)
        disk = shutil.disk_usage(self.root)
        return {"items": items, "total_bytes": sum(i["size"] for i in items if i["exists"]),
                "cleanable_bytes": sum(i["size"] for i in items if i["cleanable"] and i["exists"]),
                "disk_free_bytes": disk.free}

    @staticmethod
    def _select(items, *, artifact_ids=None, job_id=None):
        ids = set(artifact_ids) if artifact_ids is not None else None
        return [i for i in items if (ids is None or i["id"] in ids) and (job_id is None or i["job_id"] == job_id)]

    def preview(self, *, artifact_ids=None, job_id=None, force=False):
        items = self._select(self.inventory()["items"], artifact_ids=artifact_ids, job_id=job_id)
        if force:
            for item in items:
                item["cleanable"] = item["manual_cleanable"]
        return {"items": items, "reclaimable_bytes": sum(i["size"] for i in items if i["cleanable"] and i["exists"]),
                "cleanable_count": sum(i["cleanable"] for i in items), "affected_datasets": self.affected_datasets([i["id"] for i in items if i["cleanable"]])}

    def cleanup(self, *, artifact_ids=None, job_id=None, force=False, progress=None):
        # Recompute eligibility under the same writer lock used to acquire job holds.
        with self.connect(write=True) as db:
            items = self._select(self._inventory(db), artifact_ids=artifact_ids, job_id=job_id)
            if force:
                for item in items:
                    item["cleanable"] = item["manual_cleanable"]
            eligible = [i for i in items if i["cleanable"]]
            for i in eligible:
                db.execute("UPDATE artifacts SET state='deleting' WHERE id=?", (i["id"],))
        return self._delete_selected(eligible, items, progress)

    def _delete_selected(self, eligible, items, progress=None):
        deleted, errors, reclaimed = [], [], 0
        eligible_ids = {i["id"] for i in eligible}
        for index, i in enumerate(eligible):
            if progress:
                try:
                    progress(index, len(eligible))
                except BaseException:
                    with self.connect(write=True) as db:
                        for pending in eligible[index:]:
                            db.execute("UPDATE artifacts SET state='ready' WHERE id=? AND state='deleting'", (pending["id"],))
                    raise
            try:
                reclaimed += self._finish_delete(i["id"])
                deleted.append(i["id"])
            except (OSError, Conflict) as e:
                # Keep a retryable record. Never silently claim a failed unlink succeeded.
                with self.connect(write=True) as db:
                    db.execute("UPDATE artifacts SET state='ready' WHERE id=? AND state='deleting'", (i["id"],))
                errors.append({"id": i["id"], "error": str(e)})
        return {"deleted_ids": deleted, "reclaimed_bytes": reclaimed,
                "skipped": [{"id": i["id"], "protection": i["protection"]} for i in items if i["id"] not in eligible_ids], "errors": errors}

    def affected_datasets(self, artifact_ids):
        ids, affected = set(artifact_ids), []
        for item in self.inventory()["items"]:
            if item["metadata"].get("kind") != "dataset_manifest" or not item["exists"]:
                continue
            try:
                manifest = json.loads(self.path(item["id"]).read_text(encoding="utf-8"))
            except (ValueError, OSError, NotFound):
                continue
            clips = [c for c in manifest.get("clips", []) if c["artifact_id"] in ids]
            sources = [s for s in manifest.get("sources", []) if ids.intersection({s.get("id"), s.get("master_id"), s.get("original_id")})]
            if clips or sources or item["id"] in ids:
                affected.append({"id": item["id"], "name": item["name"], "clip_count": len(clips), "source_count": len(sources)})
        return affected

    def deletion_preview(self, artifact_ids):
        if not artifact_ids:
            raise ValueError("请选择要永久删除的文件")
        items = self._select(self.inventory()["items"], artifact_ids=artifact_ids)
        return {"items": items, "cleanable_count": sum(i["deletable"] for i in items),
                "reclaimable_bytes": sum(i["size"] for i in items if i["deletable"] and i["exists"]),
                "affected_datasets": self.affected_datasets(artifact_ids)}

    def delete_artifacts(self, artifact_ids, progress=None):
        if not artifact_ids:
            raise ValueError("请选择要永久删除的文件")
        with self.connect(write=True) as db:
            items = self._select(self._inventory(db), artifact_ids=artifact_ids)
            eligible = [i for i in items if i["deletable"]]
            for item in eligible:
                db.execute("UPDATE artifacts SET state='deleting' WHERE id=?", (item["id"],))
        return self._delete_selected(eligible, items, progress)

    def training_folders(self):
        groups = {}
        for item in self.inventory()["items"]:
            if item["role"] != "source" or item["metadata"].get("purpose", "training") != "training":
                continue
            singer = item["metadata"].get("singer", "未分类歌手")
            group = groups.setdefault(singer, {"singer": singer, "source_ids": [], "items": [], "bytes": 0})
            group["source_ids"].append(item["id"])
            group["items"].append(item)
            group["bytes"] += item["size"]
        return sorted(groups.values(), key=lambda group: group["singer"].casefold())

    def _finish_delete(self, artifact_id):
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=? AND state='deleting'", (artifact_id,)).fetchone()
            if not row:
                return 0
            path = self._path(row["path"])
            size = self._directory_bytes(path) if path.is_dir() else path.stat().st_size if path.is_file() else 0
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            db.execute("UPDATE artifacts SET state='deleted' WHERE id=?", (artifact_id,))
            parent = path.parent
            while parent != self.files and self.files in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
            return size

    @staticmethod
    def _directory_bytes(path):
        total = 0
        for base, directories, names in os.walk(path, followlinks=False):
            directories[:] = [name for name in directories if not is_reference(Path(base) / name)]
            for name in names:
                file = Path(base) / name
                try:
                    if not file.is_symlink() and file.is_file():
                        total += file.stat().st_size
                except FileNotFoundError:
                    continue
        return total

    def recover_deletions(self):
        with self.connect() as db:
            pending = [r[0] for r in db.execute("SELECT id FROM artifacts WHERE state='deleting'")]
        for aid in pending:
            try:
                self._finish_delete(aid)
            except (OSError, Conflict):
                with self.connect(write=True) as db:
                    db.execute("UPDATE artifacts SET state='ready' WHERE id=? AND state='deleting'", (aid,))

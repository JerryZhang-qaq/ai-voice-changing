"""Persist observations from real engine output; unknown work stays indeterminate."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time


class LogTelemetry:
    def __init__(self, store, job_id, path, stage=None, namespace=None):
        self.store, self.job_id, self.path, self.stage = store, job_id, Path(path), stage
        self.namespace = namespace
        self.offset = self.path.stat().st_size if self.path.exists() else 0
        self.pending = b""
        self.last_gpu = 0.
        self.started = time.monotonic()
        self.baseline = None

    def tick(self):
        values = {}
        if self.path.exists():
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read(512 * 1024)
                self.offset = stream.tell()
            self.pending += data
            lines = re.split(rb"[\r\n]", self.pending)
            self.pending = lines.pop()
            for line in lines:
                parsed = self.parse(line.decode("utf-8", errors="replace"))
                if parsed:
                    values.update(parsed)
        if not values:
            return
        job = self.store.job(self.job_id)
        if job["status"] != "running":
            return
        metadata = job["metadata"]
        stage = self.stage or metadata.get("stage", "引擎处理中")
        update = {"stage": stage, **values}
        training = values.pop("training", None)
        if training:
            current = {**metadata.get("training", {}), **training}
            curve = metadata.get("loss_history", [])
            if "loss_g" in training:
                point = {k: training[k] for k in ("epoch", "batch", "loss_g", "loss_d") if k in training}
                if not curve or curve[-1] != point:
                    curve = [*curve, point][-600:]
            if time.monotonic() - self.last_gpu >= 3:
                current["gpu"] = gpu_sample()
                self.last_gpu = time.monotonic()
            update.update(training=current, loss_history=curve)
        done, total = update.get("done"), update.get("total")
        if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0:
            now = time.monotonic()
            if self.baseline is None or done < self.baseline[0]:
                self.baseline = (done, now)
            elif done > self.baseline[0] and now - self.baseline[1] >= 1:
                speed = (done - self.baseline[0]) / (now - self.baseline[1])
                update["eta_seconds"] = max(0, (total - done) / speed)
        if self.namespace:
            update = {self.namespace: {**(metadata.get(self.namespace) or {}), **update}}
            batch = metadata.get("batch_progress")
            current = update[self.namespace]
            done, total = current.get("done"), current.get("total")
            if batch and isinstance(done, (float, int)) and isinstance(total, (float, int)) and total > 0:
                fraction = min(1, max(0, done / total))
                if batch.get("clip_total"):
                    fraction = (batch["clip_index"] - 1 + fraction) / batch["clip_total"]
                # Leave the final part of each step for export and quality checks.
                update["batch_progress"] = {**batch, "done": max(batch["done"], batch["completed_steps"] + .95 * fraction)}
        self.store.update_job(self.job_id, "running", metadata=update)

    @staticmethod
    def parse(line):
        try:
            event = json.loads(line)
        except ValueError:
            event = {}
        if isinstance(event, dict) and event.get("event") == "workbench_training":
            required = ("epoch", "epochs", "batch", "batches", "loss_g", "loss_d")
            if not all(isinstance(event.get(key), (float, int)) and math.isfinite(event[key]) for key in required):
                return None
            if event["batches"] <= 0 or event["epochs"] <= 0:
                return None
            done = event["epoch"] - 1 + event["batch"] / event["batches"]
            observed = {key: event[key] for key in required}
            if isinstance(event.get("step"), int) and event["step"] >= 0:
                observed["step"] = event["step"]
            return {"training": observed, "done": done, "total": event["epochs"], "unit": "epochs"}
        if isinstance(event, dict) and event.get("event") == "workbench_separation":
            values = {key: value for key, value in event.items() if key != "event"}
            if values.get("state") in {"loading", "ready", "running", "fallback"}:
                values.update(done=0, total=None, eta_seconds=None)
            return values
        match = re.search(r"(?:进度[：:]?\s*|\|\s*)(\d+)\s*/\s*(\d+)", line)
        if not match:
            match = re.search(r"\d+%.*?\|\s*(\d+)\s*/\s*(\d+)", line)
        if match and int(match[2]) > 0:
            return {"done": int(match[1]), "total": int(match[2]), "unit": "items"}
        match = re.search(r"(?:轮次[：:]\s*|Epoch[: ]+)(\d+)", line)
        if match:
            return {"training": {"epoch": int(match[1])}}
        if "Saving model and optimizer state" in line or "保存模型和优化器" in line:
            return {"checkpoint": line.strip()[-240:]}
        return None


def gpu_sample():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(["nvidia-smi", "--id=0", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=2)
        utilization, used, total = map(float, result.stdout.strip().split(","))
        return {"utilization": utilization, "used_mb": used, "total_mb": total}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None

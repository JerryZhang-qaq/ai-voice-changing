"""Actual train → FAISS → RVC conversion → mix on the target GPU.

Uses generated pitches solely to test execution, never to measure singing quality.
Stop the workbench before running this independent worker smoke check.
"""
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import traceback

import numpy as np
import soundfile as sf

from voice_workbench_storage import ArtifactStore
from voice_workbench_storage.locking import WorkerLock
from voice_workbench_dataset import prepare_dataset, SliceConfig
from voice_workbench_dataset.curation import assign_split
from voice_workbench_engines.rvc import status as rvc_status
from voice_workbench_worker.runner import run_one


def main():
    runtime = Path(os.environ.get("WORKBENCH_RUNTIME", "runtime")).resolve()
    state = rvc_status()
    if not state["ready"] or not state["training_rates"]["40k"]:
        raise SystemExit("请先安装 GPU 引擎并在资源页下载、校验 RVC 基础模型和 40k 底模")
    store = ArtifactStore(runtime)
    diagnostics = runtime / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    report = {"purpose": "execution smoke only, no quality claim", "rvc": state, "jobs": []}
    with WorkerLock(runtime / "worker.lock"):
        if any(j["status"] in {"queued", "running"} for j in store.jobs()):
            raise SystemExit("请先完成或取消工作台任务，再关闭工作台")
        def execute(kind, inputs, config):
            job = store.create_job(kind, inputs, config)
            run_one(store)
            result = store.job(job["id"])
            report["jobs"].append(result)
            if result["status"] != "completed":
                raise RuntimeError(result["error"] or result["status"])
            return result["metadata"]["result_id"]
        try:
            sr = 16000
            signals = []
            for pitch in (180, 230, 290, 370):
                t = np.arange(3*sr)/sr
                phase = 2*np.pi*(pitch*t+.7*np.sin(2*np.pi*5*t))
                signals.extend([(.15*np.sin(phase)+.035*np.sin(2*phase)).astype(np.float32), np.zeros(sr//2,np.float32)])
            audio = np.concatenate(signals)
            stream = BytesIO()
            sf.write(stream, audio, sr, format="WAV", subtype="FLOAT")
            source = store.import_stream(BytesIO(stream.getvalue()), name="GPU 执行检查信号.wav", role="source", metadata={"kind":"dry_vocal"})
            prep = store.create_job("dataset_prepare", [source["id"]])
            manifest_id = prepare_dataset(store, prep["id"], [source["id"]], SliceConfig())
            store.update_job(prep["id"], "completed", metadata={"result_id":manifest_id})
            manifest = json.loads(store.path(manifest_id).read_text(encoding="utf-8"))
            for clip in manifest["clips"]:
                clip["status"] = "accepted"
                clip["decision"] = {"origin":"execution_smoke_fixture"}
            store.promote_dataset([c["artifact_id"] for c in manifest["clips"]])
            manifest["summary"].update(accepted_count=len(manifest["clips"]), review_count=0, excluded_count=0)
            assign_split(manifest)
            dataset = store.import_stream(BytesIO(json.dumps(manifest).encode()), name="执行检查数据集.json", role="dataset", metadata={"kind":"dataset_manifest","summary":manifest["summary"]})
            model_id = execute("rvc_train", [dataset["id"], *(c["artifact_id"] for c in manifest["clips"])],
                               {"dataset_id":dataset["id"],"rate":"40k","epochs":2,"batch_size":1,"save_every":1,"resume_id":None})
            index = next(a["id"] for a in store.inventory()["items"] if a["metadata"].get("model_id")==model_id and a["metadata"].get("kind")=="rvc_index")
            converted = execute("rvc_convert", [source["id"],model_id,index], {"audio_id":source["id"],"model_id":model_id,"index_id":index})
            silence = BytesIO()
            sf.write(silence, np.zeros((len(audio),2),np.float32), sr, format="WAV", subtype="FLOAT")
            backing = store.import_stream(BytesIO(silence.getvalue()), name="执行检查伴奏.wav", role="source")
            export = execute("mix", [converted,backing["id"]], {"vocal_id":converted,"instrumental_id":backing["id"],"format":"wav"})
            report.update(status="passed", export_id=export)
            print("真实 GPU 训练、索引、转换和混音已完成。结果仅用于执行检查。")
        except Exception:
            report.update(status="failed", traceback=traceback.format_exc())
            raise
        finally:
            (diagnostics/"gpu-smoke.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()

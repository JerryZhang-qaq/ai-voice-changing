from io import BytesIO
import json
import zipfile

from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf

from voice_workbench_api.app import create_app
from voice_workbench_worker.runner import run_one


def test_export_contains_only_accepted_audio_and_grouped_manifest(tmp_path):
    app = create_app(tmp_path)
    client, store = TestClient(app), app.state.store
    sr = 16000
    audio = BytesIO()
    t = np.arange(sr * 3) / sr
    sf.write(audio, .1 * np.sin(2 * np.pi * 440 * t), sr, format="WAV")
    source = client.post("/api/sources", files={"file": ("vocal.wav", audio.getvalue())}).json()
    job = client.post("/api/datasets/prepare", json={"source_ids": [source["id"]]}).json()
    run_one(store)
    mid = store.job(job["id"])["metadata"]["result_id"]
    clip = client.get(f'/api/datasets/{mid}').json()["clips"][0]
    reviewed = client.post(f'/api/datasets/{mid}/review', json={"decisions": {clip["artifact_id"]: "accepted"}}).json()
    export = client.post(f'/api/datasets/{reviewed["id"]}/export', json={})
    assert export.status_code == 202
    run_one(store)
    complete = store.job(export.json()["id"])
    assert complete["status"] == "completed", complete
    content = client.get(f'/api/artifacts/{complete["metadata"]["result_id"]}/file').content
    with zipfile.ZipFile(BytesIO(content)) as archive:
        assert archive.namelist() == [f'train/{clip["artifact_id"]}.wav', "manifest.json"]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["validation"]["status"] == "unavailable_single_source"

"""GPU substitutes test ordering and scope; signal analysis and review are real."""
from io import BytesIO

from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf

from voice_workbench_api.app import create_app
from voice_workbench_worker.runner import run_one


def wav(x, sr=8000):
    stream = BytesIO()
    sf.write(stream, x, sr, format="WAV", subtype="FLOAT")
    return stream.getvalue()


def test_song_needs_only_vocal_model_and_dry_folder_needs_no_separator(tmp_path):
    app = create_app(tmp_path)
    store, client = app.state.store, TestClient(app)
    t = np.arange(32000) / 8000
    content = wav(.15 * np.sin(2 * np.pi * 220 * t))
    store.set_runtime("engines", {"separation": {"models": [
        {"id": "vocals_melband_unwa", "ready": True},
        {"id": "lead_melband_aufr33", "ready": False},
    ]}})
    for kind, singer in (("song", "歌曲歌手"), ("dry_vocal", "干声歌手")):
        source = client.post("/api/sources", files={"file": ("曲目.wav", content)},
                             data={"singer": singer, "kind": kind}).json()
        response = client.post("/api/datasets/prepare", json={"folder_singer": singer, "solo_confirmed": True})
        assert response.status_code == 202, response.text
        assert response.json()["metadata"]["preprocessing"]["check_harmony"] is False
        if kind == "dry_vocal":
            assert run_one(store)  # The earlier song is cancelled below.
            assert store.job(response.json()["id"])["status"] == "completed"
        else:
            client.post(f"/api/jobs/{response.json()['id']}/cancel")
        requested = client.post("/api/datasets/prepare", json={"source_ids": [source["id"]], "check_harmony": True})
        assert requested.status_code == 409


def test_harmony_runs_on_clips_and_one_warning_does_not_discard_song(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    store, client = app.state.store, TestClient(app)
    store.set_runtime("engines", {"separation": {"models": [
        {"id": "vocals_melband_unwa", "ready": True},
        {"id": "lead_melband_aufr33", "ready": True},
    ]}})
    t = np.arange(8000 * 13) / 8000
    content = wav(.15 * np.sin(2 * np.pi * (220 * t + .5 * t**2)))
    calls, harmony_clips = [], []

    def separation(store, job_id, source_id, model_id, *, keep_stems=None, **kwargs):
        source = store.get(source_id)
        calls.append((model_id, source_id))
        x, sr = sf.read(store.path(source_id), dtype="float32")
        if model_id == "vocals_melband_unwa":
            assert "interval" not in source["metadata"]
            assert keep_stems == ["vocals"]
            outputs = {"vocals": x}
        else:
            assert model_id == "lead_melband_aufr33"
            assert source["metadata"]["interval"]["duration"] <= 6
            harmony_clips.append(source_id)
            # Only the first clip contains an independently pitched backing.
            backing = .08 * np.sin(2 * np.pi * 330 * np.arange(len(x)) / sr) if len(harmony_clips) == 1 else np.zeros_like(x)
            outputs = {"lead": x, "backing": backing}
        return {stem: store.import_stream(BytesIO(wav(x, sr)), name=f"{source['name']}-{stem}.wav", job_id=job_id,
                                         metadata={"stem": stem, "kind": "separated_audio", "source_id": source_id})["id"]
                for stem, x in outputs.items()}

    monkeypatch.setattr("voice_workbench_worker.runner.separate", separation)
    source = client.post("/api/sources", files={"file": ("曲目.wav", content)},
                         data={"singer": "测试歌手", "kind": "song"}).json()
    response = client.post("/api/datasets/prepare", json={"folder_singer": "测试歌手", "solo_confirmed": True,
                                                          "check_harmony": True, "target_seconds": 4, "max_seconds": 6})
    assert response.status_code == 202, response.text
    assert run_one(store)
    job = store.job(response.json()["id"])
    assert job["status"] == "completed", job
    result_id = job["metadata"]["result_id"]
    manifest = client.get(f"/api/datasets/{result_id}").json()
    clips = manifest["clips"]
    assert len(clips) >= 2
    assert calls[0] == ("vocals_melband_unwa", source["id"])
    assert harmony_clips == [c["artifact_id"] for c in clips]
    assert manifest["summary"]["excluded_source_count"] == 0
    flagged = [c for c in clips if "COMPLEX_HARMONY" in c["reasons"]]
    assert len(flagged) == manifest["summary"]["harmony_review_count"] == 1
    assert flagged[0]["status"] == "review"
    assert all(c["status"] != "excluded" for c in clips)
    assert all(c["harmony"]["scope"] == "clip" for c in clips)
    assert all(c["harmony"]["backing_available"] and c["harmony"]["report_available"] for c in clips)
    assert not clips[1]["reasons"].count("COMPLEX_HARMONY")
    # The user can accept even the warned clip, without changing the old version.
    saved = client.post(f"/api/datasets/{result_id}/review", json={"decisions": {flagged[0]["artifact_id"]: "accepted"}})
    assert saved.status_code == 201, saved.text
    assert client.get(f"/api/datasets/{saved.json()['id']}").json()["summary"]["accepted_count"] == 1
    assert client.get(f"/api/datasets/{result_id}").json()["summary"]["accepted_count"] == 0
    # Editing the warned boundary cannot retain evidence for the old clip.
    edited = client.post(f"/api/datasets/{result_id}/edit", json={"clip_id": flagged[0]["artifact_id"],
                         "start_seconds": flagged[0]["valid_start_sample"] / flagged[0]["sample_rate"] + .1,
                         "end_seconds": flagged[0]["valid_end_sample"] / flagged[0]["sample_rate"] - .1})
    assert edited.status_code == 202, edited.text
    assert run_one(store)
    state = store.job(edited.json()["id"])
    assert state["status"] == "completed", state
    new_clip = client.get(f"/api/datasets/{state['metadata']['result_id']}").json()["clips"][0]
    assert new_clip["harmony"] == {"status": "not_checked", "scope": "clip"}
    assert "COMPLEX_HARMONY" not in new_clip["reasons"]

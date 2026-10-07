import hashlib
import json
from io import BytesIO

import numpy as np
import pytest
import soundfile as sf

from voice_workbench_dataset.analysis import analyze_voice
from voice_workbench_dataset.harmony import assess_harmony
from voice_workbench_dataset.admission import PreparationPolicy
from voice_workbench_dataset.pipeline import prepare_dataset
from voice_workbench_dataset.segmentation import SliceConfig, segment
from voice_workbench_storage import ArtifactStore


def tone(freq=220, amplitude=.2, seconds=4):
    t = np.arange(round(8000 * seconds)) / 8000
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_pitch_tracks_fundamental_and_does_not_label_noise_voiced():
    t = np.arange(8000 * 4) / 8000
    signal = tone() + .05 * np.sin(2 * np.pi * 440 * t)
    voice = analyze_voice(signal, 8000)
    assert voice.summary()["voiced_fraction"] > .95
    assert abs(voice.summary()["f0_p90_hz"] - 220) < 1
    noise = analyze_voice(np.random.default_rng(0).normal(0, .1, len(t)).astype(np.float32), 8000)
    assert noise.summary()["voiced_fraction"] < .05


def test_independent_harmony_warns_but_weak_leakage_passes_and_octaves_need_review():
    lead = analyze_voice(tone(), 8000)
    complex_clip = assess_harmony(lead, analyze_voice(tone(330, .08), 8000))
    assert complex_clip["status"] == "review"
    assert complex_clip["reasons"] == ["COMPLEX_HARMONY"]
    assert assess_harmony(lead, analyze_voice(tone(220, .002), 8000))["status"] == "passed"
    assert assess_harmony(lead, analyze_voice(tone(440, .12), 8000))["status"] == "review"


def test_long_singing_has_bounded_context_and_complete_core_coverage():
    x = tone(seconds=31)
    voice = analyze_voice(x, 8000)
    clips, _ = segment(x, 8000, SliceConfig(max_seconds=10, target_seconds=8, padding_seconds=.5), voice=voice)
    assert all(c["duration"] <= 10 for c in clips)
    assert clips[0]["valid_start_sample"] == 0 and clips[-1]["valid_end_sample"] == len(x)
    assert all(a["valid_end_sample"] == b["valid_start_sample"] for a, b in zip(clips, clips[1:]))
    assert all("UNSAFE_BOUNDARY" in c["reasons"] for c in clips)


def import_tone(store):
    stream = BytesIO()
    sf.write(stream, tone(), 8000, format="WAV", subtype="FLOAT")
    return store.import_stream(BytesIO(stream.getvalue()), name="solo.wav", role="source", metadata={"kind": "dry_vocal"})["id"]


def test_auto_accept_requires_declaration_and_clip_harmony_evidence_and_protects_audio(tmp_path):
    with pytest.raises(ValueError, match="同一歌手"):
        PreparationPolicy("automatic", False).validate()
    store = ArtifactStore(tmp_path)
    aid = import_tone(store)
    job = store.create_job("dataset_prepare", [aid])
    result = prepare_dataset(store, job["id"], [aid], SliceConfig(), policy=PreparationPolicy("automatic", True),
                             harmony_checker=lambda clip_id: {"status": "passed"})
    manifest = json.loads(store.path(result).read_text())
    assert manifest["summary"]["accepted_count"] == 1
    clip = manifest["clips"][0]
    assert clip["decision"]["origin"] == "automatic"
    store.update_job(job["id"], "completed")
    store.cleanup()
    assert store.path(clip["artifact_id"]).is_file()


def test_all_fifteen_sources_are_sliced_despite_fourteen_legacy_harmony_rejections(tmp_path):
    store = ArtifactStore(tmp_path)
    aids = []
    for i in range(15):
        stream = BytesIO()
        sf.write(stream, tone(freq=180 + i * 13), 8000, format="WAV", subtype="FLOAT")
        aids.append(store.import_stream(BytesIO(stream.getvalue()), name=f"曲目{i + 1}.wav", role="source",
                                       metadata={"kind": "dry_vocal", "singer": "测试歌手"})["id"])
    records = [{"original_id": aid, "stages": [], "harmony": {"status": "rejected" if i < 14 else "passed"}}
               for i, aid in enumerate(aids)]
    config, policy = SliceConfig(), PreparationPolicy("automatic", True)
    # Fixture uses the published 0.0.3 cache format, not the new identity.
    old_key = hashlib.sha256(json.dumps({"sources": [(aid, store.get(aid)["sha256"]) for aid in aids],
        "config": config.dict(), "processor": "solo-pipeline-4", "processing": records, "policy": policy.dict()}, sort_keys=True).encode()).hexdigest()
    legacy = store.import_stream(BytesIO(json.dumps({"schema_version": 4, "clips": [], "summary": {"excluded_source_count": 14}}).encode()),
                                 name="测试歌手-after.json", role="dataset", metadata={"kind": "dataset_manifest", "cache_key": old_key})
    job = store.create_job("dataset_prepare", aids)
    result = prepare_dataset(store, job["id"], aids, config, policy=policy, processing_records=records)
    assert result != legacy["id"], "Old cached rejections must not hide the songs again"
    manifest = json.loads(store.path(result).read_text())
    assert len(manifest["sources"]) == 15
    assert {c["source_id"] for c in manifest["clips"]} == set(aids)
    assert manifest["summary"]["excluded_source_count"] == 0
    assert all("COMPLEX_HARMONY" not in c["reasons"] for c in manifest["clips"])
    assert manifest["summary"]["accepted_count"] == 0, "Legacy song-level passing evidence cannot accept clips"


def test_optional_clip_harmony_does_not_reuse_unscreened_manifest(tmp_path):
    store = ArtifactStore(tmp_path)
    aid = import_tone(store)
    first = store.create_job("dataset_prepare", [aid])
    plain = prepare_dataset(store, first["id"], [aid], SliceConfig())
    second = store.create_job("dataset_prepare", [aid])
    calls = []

    def checker(clip_id):
        calls.append(clip_id)
        assert store.get(clip_id)["metadata"]["interval"]
        return {"status": "review", "reasons": ["COMPLEX_HARMONY"]}

    screened = prepare_dataset(store, second["id"], [aid], SliceConfig(), harmony_checker=checker)
    assert screened != plain
    manifest = json.loads(store.path(screened).read_text())
    assert calls == [c["artifact_id"] for c in manifest["clips"]]
    assert manifest["clips"][0]["status"] == "review"
    assert manifest["summary"]["harmony_review_count"] == 1

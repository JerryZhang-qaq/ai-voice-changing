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


def test_independent_harmony_rejects_but_weak_leakage_passes_and_octaves_need_review():
    lead = analyze_voice(tone(), 8000)
    assert assess_harmony(lead, analyze_voice(tone(330, .08), 8000))["status"] == "rejected"
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


def test_auto_accept_requires_solo_and_harmony_evidence_and_protects_audio(tmp_path):
    with pytest.raises(ValueError, match="独唱"):
        PreparationPolicy("automatic", False).validate()
    store = ArtifactStore(tmp_path)
    aid = import_tone(store)
    job = store.create_job("dataset_prepare", [aid])
    result = prepare_dataset(store, job["id"], [aid], SliceConfig(), policy=PreparationPolicy("automatic", True),
                             processing_records=[{"original_id": aid, "stages": [], "harmony": {"status": "passed"}}])
    manifest = json.loads(store.path(result).read_text())
    assert manifest["summary"]["accepted_count"] == 1
    clip = manifest["clips"][0]
    assert clip["decision"]["origin"] == "automatic"
    store.update_job(job["id"], "completed")
    store.cleanup()
    assert store.path(clip["artifact_id"]).is_file()


def test_complex_harmony_discards_entire_source_before_slice(tmp_path):
    store = ArtifactStore(tmp_path)
    aid = import_tone(store)
    job = store.create_job("dataset_prepare", [aid])
    result = prepare_dataset(store, job["id"], [aid], SliceConfig(), policy=PreparationPolicy("automatic", True),
                             processing_records=[{"original_id": aid, "stages": [], "harmony": {"status": "rejected"}}])
    manifest = json.loads(store.path(result).read_text())
    assert manifest["clips"] == []
    assert manifest["summary"]["excluded_source_count"] == 1
    assert manifest["sources"][0]["reasons"] == ["COMPLEX_HARMONY"]

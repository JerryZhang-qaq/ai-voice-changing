import numpy as np
import pytest
import soundfile as sf

from voice_workbench_engines.rvc import resample_for_training, validate_features
from voice_workbench_engines.rvc import training_chunks
from voice_workbench_storage import ArtifactStore
from voice_workbench_engines.process import EngineError


def test_training_format_preserves_quiet_amplitude_and_duration(tmp_path):
    sr = 16000
    x = (.015 * np.sin(2 * np.pi * 220 * np.arange(sr * 3) / sr)).astype(np.float32)
    original, output = tmp_path / "input.wav", tmp_path / "output.wav"
    sf.write(original, x, sr, subtype="FLOAT")
    resample_for_training(original, output, 40000)
    y, rate = sf.read(output)
    assert rate == 40000
    assert len(y) / rate == pytest.approx(3, abs=.001)
    assert np.max(np.abs(y)) < .017  # Must not normalize a quiet clip to .9.


def test_zero_or_partial_feature_extraction_cannot_start_training(tmp_path):
    with pytest.raises(EngineError):
        validate_features(tmp_path, ["missing"])
    with pytest.raises(EngineError):
        validate_features(tmp_path, [])


def test_valid_features_produce_existing_training_filelist(tmp_path):
    for d in ("0_gt_wavs", "3_feature768", "2a_f0", "2b-f0nsf"):
        (tmp_path / d).mkdir()
    sf.write(tmp_path / "0_gt_wavs/a.wav", np.zeros(100), 40000)
    np.save(tmp_path / "3_feature768/a.npy", np.ones((10, 768)))
    np.save(tmp_path / "2a_f0/a.wav.npy", np.ones(20))
    np.save(tmp_path / "2b-f0nsf/a.wav.npy", np.ones(20) * 220)
    validate_features(tmp_path, ["a"])
    assert len((tmp_path / "filelist.txt").read_text().splitlines()) == 1
    for path in (tmp_path / "filelist.txt").read_text().split('|')[:-1]:
        assert __import__('pathlib').Path(path).is_file()


def test_long_phrase_fits_rvc_buckets_without_losing_tail(tmp_path):
    store = ArtifactStore(tmp_path / "runtime")
    path = tmp_path / "phrase.wav"
    sr = 16000
    sf.write(path, .02 * np.sin(2 * np.pi * 440 * np.arange(sr * 15) / sr), sr, subtype="FLOAT")
    artifact = store.import_file(path, name="phrase.wav", role="dataset")
    workspace = tmp_path / "training"
    workspace.mkdir()
    ids = training_chunks(store, [{"artifact_id": artifact["id"]}], workspace, 40000)
    import json
    chunks = json.loads((workspace / "training_audio_manifest.json").read_text())
    assert len(ids) == 3
    assert chunks[0]["valid_start_sample"] == 0
    assert chunks[-1]["valid_end_sample"] == sr * 15
    assert all(a["valid_end_sample"] == b["valid_start_sample"] for a,b in zip(chunks,chunks[1:]))
    assert all(100 < (workspace / "0_gt_wavs" / f"{i}.wav").stat().st_size // 1200 <= 900 for i in ids)

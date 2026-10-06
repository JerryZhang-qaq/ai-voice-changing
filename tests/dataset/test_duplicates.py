import numpy as np

from voice_workbench_dataset.duplicates import NearDuplicateIndex, waveform_match


SR = 16000


def performance(sr=SR, variation=0.):
    t = np.arange(sr * 4) / sr
    rng = np.random.default_rng(7)
    envelope = .2 * (1 + .6 * np.sin(2 * np.pi * 1.7 * t))
    return (envelope * np.sin(2 * np.pi * (230 * t + 3 * np.sin(2 * np.pi * (1.2 + variation) * t))) + rng.normal(0, .001, len(t))).astype(np.float32)


def test_gain_polarity_and_resampling_copies_detected_without_deletion(tmp_path):
    index = NearDuplicateIndex(tmp_path)
    assert index.add("first", performance(), SR)["matches"] == []
    copied = index.add("copy", -.25 * performance(32000), 32000)
    assert copied["matches"][0]["artifact_id"] == "first"
    assert copied["matches"][0]["correlation"] > .992
    assert copied["matches"][0]["polarity"] == -1
    assert copied["scope"] == "this_preparation_job"
    different = index.add("other", performance(variation=.08), SR)
    assert different["matches"] == []


def test_bounded_timing_offset_and_similarity_confirmation(tmp_path):
    index = NearDuplicateIndex(tmp_path)
    x = performance()
    index.add("first", x, SR)
    shift = round(.035 * SR)
    result = index.add("shifted", np.r_[np.zeros(shift), x[:-shift]], SR)
    assert result["matches"]
    assert abs(abs(result["matches"][0]["lag_seconds"]) - .035) < .002


def test_sustained_tones_cannot_prove_same_recording(tmp_path):
    index = NearDuplicateIndex(tmp_path)
    t = np.arange(SR * 3) / SR
    x = (.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    assert index.add("a", x, SR)["status"] == "inconclusive_low_variation"
    assert index.add("b", -.5 * x, SR)["matches"] == []


def test_correlation_not_biased_by_missing_overlap():
    rng = np.random.default_rng(3)
    a = rng.normal(size=5000).astype(np.float32)
    assert waveform_match(a, -2 * a)["correlation"] > .99999
    assert waveform_match(a, rng.normal(size=5000))["correlation"] < .1

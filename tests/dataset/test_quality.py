import numpy as np
import soundfile as sf

from voice_workbench_dataset.quality import compare_features, features_array, features_file


SR = 16000


def vocal():
    t = np.arange(SR * 6) / SR
    envelope = np.where((t > .5) & (t < 5.5), .12 * (1 + .65 * np.sin(2 * np.pi * 1.7 * t))**2, 0)
    return (envelope * np.sin(2 * np.pi * (220 * t + .7 * np.sin(2 * np.pi * 5 * t)))).astype(np.float32)


def compare(x, y, task="dereverb"):
    return compare_features(features_array(x, SR), features_array(y, SR), task)


def test_harmless_gain_and_polarity_do_not_trigger_preservation_damage():
    x = vocal()
    report = compare(x, -.5 * x)
    assert report["decision"] == "candidate"
    assert report["reasons"] == ["CLEANUP_COMPARISON_UNCALIBRATED"]
    assert abs(report["metrics"]["median_level_change_db"] + 6.0206) < .01


def test_complete_signal_collapse_falls_back_only_for_vocal_cleanup():
    x = vocal()
    damaged = np.zeros_like(x)
    for task in ("dereverb", "lead_backing"):
        report = compare(x, damaged, task)
        assert report["decision"] == "fallback_input"
        assert "CLEANUP_SIGNAL_COLLAPSE" in report["reasons"]
        assert "CLEANUP_FALLBACK_REQUIRES_REVIEW" in report["reasons"]
    extraction = compare(x, damaged, "vocals")
    assert extraction["decision"] == "candidate"
    assert "CLEANUP_SIGNAL_COLLAPSE" not in extraction["reasons"]
    assert extraction["mode"] == "changed_content"


def test_partial_loss_marks_review_and_shift_or_trim_triggers_fallback():
    x = vocal()
    missing = x.copy()
    missing[SR * 2:SR * 4] = 0
    review = compare(x, missing)
    assert review["decision"] == "candidate"
    assert "POSSIBLE_ACTIVITY_DAMAGE" in review["reasons"]
    shifted = np.r_[np.zeros(round(.12 * SR)), x[:-round(.12 * SR)]]
    offset = compare(x, shifted)
    assert offset["decision"] == "fallback_input"
    assert "CLEANUP_TIMING_SHIFT" in offset["reasons"]
    trimmed = compare(x, x[:-SR])
    assert trimmed["decision"] == "fallback_input"
    assert "CLEANUP_DURATION_CHANGED" in trimmed["reasons"]


def test_high_frequency_and_weak_activity_losses_are_visible():
    t = np.arange(SR * 6) / SR
    low = .12 * np.sin(2 * np.pi * 700 * t)
    high = .04 * np.sin(2 * np.pi * 4500 * t)
    report = compare((low + high).astype(np.float32), low.astype(np.float32))
    assert "POSSIBLE_HIGH_FREQUENCY_DAMAGE" in report["reasons"]
    x = vocal()
    x[SR * 2:SR * 3] *= .015
    y = x.copy()
    y[SR * 2:SR * 3] = 0
    report = compare(x, y)
    assert "WEAK_ACTIVITY_LOSS" in report["reasons"]


def test_file_feature_stream_preserves_antiphase_vocal(tmp_path):
    x = vocal()
    path = tmp_path / "stereo.wav"
    sf.write(path, np.column_stack((x, -x)), SR, subtype="FLOAT")
    features = features_file(path, tmp_path)
    assert features.channel["strategy"] == "single_channel"
    np.testing.assert_allclose(features.rms_db, features_array(x, SR).rms_db, atol=.01)
    assert features.peak > .1

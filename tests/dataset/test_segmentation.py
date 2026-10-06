import numpy as np

from voice_workbench_dataset import SliceConfig, segment
from voice_workbench_audio import mono


SR = 16000


def tone(seconds, amplitude=.2):
    t = np.arange(round(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_silence_does_not_produce_training_clips():
    clips, report = segment(np.zeros(SR * 5, dtype=np.float32), SR)
    assert clips == []
    assert report["activity_seconds"] == 0


def test_quiet_singing_and_unvoiced_breath_are_retained():
    rng = np.random.default_rng(4)
    breath = rng.normal(0, .002, SR // 2).astype(np.float32)
    x = np.concatenate([np.zeros(SR // 2), tone(2, .008), breath, tone(2), np.zeros(SR // 2)])
    clips, _ = segment(x, SR)
    assert len(clips) == 1
    assert clips[0]["start_sample"] < SR // 2
    assert clips[0]["end_sample"] > SR * 5
    assert "UNSAFE_BOUNDARY" not in clips[0]["reasons"]


def test_pauses_are_preferred_over_hard_cuts():
    x = np.concatenate([tone(4), np.zeros(SR), tone(5)])
    clips, _ = segment(x, SR)
    assert len(clips) == 2
    assert clips[0]["end_sample"] < clips[1]["start_sample"]
    assert all("UNSAFE_BOUNDARY" not in c["reasons"] for c in clips)


def test_continuous_long_note_marked_for_review_with_complete_coverage():
    x = tone(35)
    clips, _ = segment(x, SR)
    assert len(clips) >= 3
    assert clips[0]["valid_start_sample"] == 0
    assert clips[-1]["valid_end_sample"] == len(x)
    assert all(a["valid_end_sample"] == b["valid_start_sample"] for a, b in zip(clips, clips[1:]))
    assert all(c["duration"] <= 15.01 for c in clips)
    assert all("UNSAFE_BOUNDARY" in c["reasons"] for c in clips)


def test_antiphase_channels_do_not_cancel_voice():
    x = tone(3)
    output, report = mono(np.column_stack((x, -x)))
    assert np.max(np.abs(output)) > .19
    assert report["strategy"] == "single_channel"


def test_deterministic_boundaries():
    x = np.concatenate([tone(4), np.zeros(SR), tone(20)])
    assert segment(x, SR) == segment(x, SR)

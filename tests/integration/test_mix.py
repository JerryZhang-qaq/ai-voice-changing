import numpy as np
import pytest
import soundfile as sf

from voice_workbench_audio.mix import mix
from voice_workbench_audio.io import AudioError
from voice_workbench_storage import ArtifactStore


def add(store, tmp_path, name, seconds, gain, channels=1):
    sr = 16000
    t = np.arange(round(sr * seconds)) / sr
    audio = gain * np.sin(2 * np.pi * 440 * t)
    if channels == 2:
        audio = np.column_stack((audio, .5 * audio))
    path = tmp_path / name
    sf.write(path, audio, sr, subtype="FLOAT")
    return store.import_file(path, name=name)["id"]


def test_mix_preserves_alignment_stereo_and_prevents_clipping(tmp_path):
    store = ArtifactStore(tmp_path / "runtime")
    vocal = add(store, tmp_path, "vocal.wav", 3, .8)
    inst = add(store, tmp_path, "inst.wav", 3, .7, 2)
    job = store.create_job("mix", [vocal, inst])
    result = mix(store, job["id"], vocal, inst)
    x, rate = sf.read(store.path(result))
    assert rate == 44100 and x.shape == (rate * 3, 2)
    assert np.max(np.abs(x)) < .9
    assert np.max(np.abs(x[:, 0])) > np.max(np.abs(x[:, 1]))
    assert store.get(result)["metadata"]["peak_attenuation_db"] < 0
    store.update_job(job["id"], "completed")
    assert result not in store.cleanup()["deleted_ids"]


def test_misaligned_tracks_fail_instead_of_silent_truncation(tmp_path):
    store = ArtifactStore(tmp_path / "runtime")
    a = add(store, tmp_path, "a.wav", 3, .1)
    b = add(store, tmp_path, "b.wav", 4, .1)
    job = store.create_job("mix", [a, b])
    with pytest.raises(AudioError, match="时长"):
        mix(store, job["id"], a, b)

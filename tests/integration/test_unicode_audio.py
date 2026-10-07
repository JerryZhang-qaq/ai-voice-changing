"""Real FFprobe/FFmpeg with Unicode singer, folder and song names on both OSes."""
import numpy as np
import soundfile as sf
from voice_workbench_audio.io import probe, decode


def test_chinese_and_japanese_audio_paths_roundtrip(tmp_path):
    folder = tmp_path / '歌手' / '日本歌手名字'
    folder.mkdir(parents=True)
    source = folder / '中文歌曲 日本語.wav'
    sr = 16000
    x = .1 * np.sin(2 * np.pi * 220 * np.arange(sr * 2) / sr)
    sf.write(source, x, sr, subtype='FLOAT')
    info = probe(source)
    assert info['duration'] == 2 and info['sample_rate'] == sr
    output = folder / '解码后的工作母版.wav'
    decode(source, output)
    y, rate = sf.read(output)
    assert rate == sr and len(y) == len(x) and np.max(np.abs(y-x)) < 1e-6

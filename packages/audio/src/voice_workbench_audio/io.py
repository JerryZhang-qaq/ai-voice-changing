from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import soundfile as sf


class AudioError(ValueError):
    pass


def probe(path: Path):
    result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AudioError("音频无法解码，请检查文件格式和完整性")
    data = json.loads(result.stdout)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise AudioError("文件中没有音轨")
    stream = streams[0]
    duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
    if not np.isfinite(duration) or duration <= 0:
        raise AudioError("无法确定有效音频时长")
    if duration > 1800:
        raise AudioError("单个文件暂限 30 分钟，请分批上传")
    sample_rate, channels = int(stream.get("sample_rate", 0)), int(stream.get("channels", 0))
    if not 8000 <= sample_rate <= 192000 or not 1 <= channels <= 8:
        raise AudioError("不支持的采样率或声道数量")
    return {"duration": duration, "sample_rate": sample_rate, "channels": channels, "codec": stream.get("codec_name")}


def decode(source: Path, destination: Path, *, target_rate: int | None = None):
    info = probe(source)
    # Decoded work masters use <=48 kHz and <=2 channels to bound memory use.
    if target_rate is not None and not 8000 <= target_rate <= 48000:
        raise AudioError("工作采样率超出允许范围")
    rate = min(info["sample_rate"], target_rate or 48000)
    channels = min(info["channels"], 2)
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:a:0", "-vn", "-t", "1800", "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_f32le", "-y", str(destination)], capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise AudioError("音频解码失败")
    with sf.SoundFile(destination) as audio:
        if not audio.frames:
            raise AudioError("解码结果没有有效采样点")
        info.update(decoded_rate=audio.samplerate, decoded_frames=audio.frames, decoded_channels=audio.channels)
    if info["channels"] > 2:
        info["requires_review"] = ["MULTICHANNEL_DOWNMIX"]
    return info


def inspect_audio(x: np.ndarray, sample_rate: int):
    if x.size == 0:
        raise AudioError("空音频")
    if not np.isfinite(x).all():
        raise AudioError("音频含非有限采样值")
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return {"duration": len(x) / sample_rate, "peak": peak, "rms_db": float(20 * np.log10(max(rms, 1e-12))),
            "clipping_ratio": float(np.mean(np.abs(x) >= 0.999)), "dc_offset": float(np.mean(x, dtype=np.float64))}


def mono(x: np.ndarray):
    if x.ndim == 1:
        return x.astype(np.float32, copy=False), {"strategy": "mono"}
    if x.shape[1] == 1:
        return x[:, 0], {"strategy": "mono"}
    energies = np.mean(np.square(x, dtype=np.float64), axis=0)
    # Correlation of constant/empty channels is undefined; avoid propagating NaN.
    corr = float(np.corrcoef(x[::10, 0], x[::10, 1])[0, 1]) if np.min(energies) > 1e-12 else 0.0
    if not np.isfinite(corr):
        corr = 0.0
    if corr < -0.2:
        idx = int(np.argmax(energies))
        return x[:, idx], {"strategy": "single_channel", "channel": idx, "correlation": corr, "requires_review": ["PHASE_CANCELLATION"]}
    return x.mean(axis=1, dtype=np.float32), {"strategy": "mean", "correlation": corr}

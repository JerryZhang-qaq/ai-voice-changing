"""Singing-aware evidence used for boundaries and diagnostics, never identity.

Pitch uses normalized autocorrelation with sub-sample peak interpolation. Pitch
absence alone never removes a frame: breaths, consonants and fry may be unvoiced.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from voice_workbench_audio import decode, mono


ANALYSIS_VERSION = "singing-signal-1"


@dataclass
class VoiceAnalysis:
    times: np.ndarray
    rms_db: np.ndarray
    f0: np.ndarray
    confidence: np.ndarray
    flux: np.ndarray
    duration: float

    def region(self, start, end):
        keep = (self.times >= start) & (self.times < end)
        return VoiceAnalysis(self.times[keep], self.rms_db[keep], self.f0[keep], self.confidence[keep], self.flux[keep], end - start)

    def summary(self):
        voiced = (self.confidence >= .65) & (self.f0 > 0)
        pitches = self.f0[voiced]
        midi = 69 + 12 * np.log2(pitches / 440) if len(pitches) else np.array([])
        return {"version": ANALYSIS_VERSION, "duration": self.duration,
                "voiced_fraction": float(voiced.mean()) if len(voiced) else 0.,
                "voiced_seconds": float(voiced.sum() * .02),
                "f0_p10_hz": float(np.percentile(pitches, 10)) if len(pitches) else None,
                "f0_p90_hz": float(np.percentile(pitches, 90)) if len(pitches) else None,
                "pitch_histogram": np.histogram(midi, bins=np.arange(24, 109, 3))[0].tolist(),
                "pitch_histogram_midi_edges": list(range(24, 109, 3))}


def analyze_voice(x, sr, progress=None):
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all():
        raise ValueError("歌唱分析需要有效单声道音频")
    hop, window = round(sr * .02), round(sr * .08)
    starts = np.arange(0, len(x), hop)
    n_fft = 1 << (2 * window - 1).bit_length()
    lag_min, lag_max = max(2, round(sr / 1500)), min(window - 2, round(sr / 60))
    padded = np.pad(x, (window // 2, window))
    rms, pitches, confidence, spectra = [], [], [], []
    previous_spectrum = None
    taper = np.hanning(window)
    for offset in range(0, len(starts), 256):
        if progress:
            progress()
        positions = starts[offset:offset + 256, None] + np.arange(window)[None, :]
        frames = padded[positions].astype(np.float64)
        frames -= frames.mean(axis=1, keepdims=True)
        power = frames**2
        rms.append(10 * np.log10(np.maximum(power.mean(axis=1), 1e-14)))
        fourier = np.fft.rfft(frames, n=n_fft, axis=1)
        autocorr = np.fft.irfft(fourier * fourier.conj(), n=n_fft, axis=1)[:, :window]
        cumulative = np.concatenate((np.zeros((len(frames), 1)), np.cumsum(power, axis=1)), axis=1)
        lags = np.arange(window)
        denominator = cumulative[:, window - lags] + cumulative[:, -1:] - cumulative[:, lags]
        nsdf = 2 * autocorr / np.maximum(denominator, 1e-14)
        peaks = (nsdf[:, 1:-1] > nsdf[:, :-2]) & (nsdf[:, 1:-1] >= nsdf[:, 2:])
        scores = np.where(peaks[:, lag_min - 1:lag_max], nsdf[:, lag_min:lag_max + 1], 0)
        strongest = scores.max(axis=1)
        eligible = scores >= np.maximum(.55, strongest[:, None] * .92)
        lag = eligible.argmax(axis=1) + lag_min
        rows = np.arange(len(frames))
        left, center, right = nsdf[rows, lag - 1], nsdf[rows, lag], nsdf[rows, lag + 1]
        curvature = left - 2 * center + right
        interpolation = np.divide(.5 * (left - right), curvature, out=np.zeros(len(frames)), where=np.abs(curvature) > 1e-12)
        valid = eligible.any(axis=1) & (rms[-1] > -75)
        pitches.append(np.where(valid, sr / (lag + np.clip(interpolation, -.5, .5)), 0).astype(np.float32))
        confidence.append(np.where(valid, np.clip(center, 0, 1), 0).astype(np.float32))
        spectrum = np.abs(np.fft.rfft(frames * taper, axis=1))
        spectrum /= np.maximum(np.linalg.norm(spectrum, axis=1, keepdims=True), 1e-12)
        earlier = np.vstack([previous_spectrum if previous_spectrum is not None else spectrum[0], spectrum[:-1]])
        spectra.append(np.maximum(spectrum - earlier, 0).sum(axis=1).astype(np.float32))
        previous_spectrum = spectrum[-1]
    flux = np.concatenate(spectra)
    flux /= max(float(np.percentile(flux, 95)), 1e-6)
    return VoiceAnalysis(starts / sr, np.concatenate(rms), np.concatenate(pitches), np.concatenate(confidence), np.clip(flux, 0, 5), len(x) / sr)


def analyze_file(path, directory, progress=None):
    proxy = Path(directory) / "analysis-proxy.wav"
    decode(Path(path), proxy, target_rate=8000)
    x, sr = sf.read(proxy, dtype="float32", always_2d=True)
    signal, channel = mono(x)
    result = analyze_voice(signal, sr, progress)
    return result, channel

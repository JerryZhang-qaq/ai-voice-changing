"""Gain/polarity tolerant candidates, confirmed by audio correlation.

This only proposes reviews and links source split groups. It never deletes a
performance, claims singer identity, or treats a melody match as a duplicate.
Analysis proxies live in the job's temporary directory, not in RAM for hours.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


DUPLICATE_VERSION = "waveform-near-duplicate-1"
PROXY_RATE = 2000


def _proxy(x, sr):
    """Fourier lowpass/downsample, only for analysis, never for training audio."""
    x = np.asarray(x, dtype=np.float32)
    if len(x) < 2 or not np.isfinite(x).all():
        raise ValueError("重复检查输入无效")
    size = max(2, round(len(x) * PROXY_RATE / sr))
    centered = x - float(x.mean(dtype=np.float64))
    spectrum = np.fft.rfft(centered)
    truncated = spectrum[:size // 2 + 1].copy()
    if size < len(x) and size % 2 == 0:
        truncated[-1] *= 2
    result = (np.fft.irfft(truncated, n=size) * size / len(x)).astype(np.float32)
    energy = float(np.sqrt(np.mean(result.astype(np.float64)**2)))
    return result / max(energy, 1e-8)


def _signature(proxy):
    frame = 100  # 50 ms; no padding truncation of the underlying proxy.
    frames = np.pad(proxy, (0, (-len(proxy)) % frame)).reshape(-1, frame)
    energy = np.sqrt(np.mean(frames.astype(np.float64)**2, axis=1))
    variation = float(np.std(energy) / max(float(energy.mean()), 1e-8))
    # Frequency shape chooses a shortlist. Raw waveform confirmation is still
    # required; two phrases with similar notes are not enough.
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(frame), axis=1))**2
    features = np.log1p(spectrum.mean(axis=0))
    features /= max(float(np.linalg.norm(features)), 1e-8)
    return features.astype(np.float32), variation


def waveform_match(a, b, max_shift_seconds=.1):
    """Bounded delay search with actual overlap-normalized signed correlation."""
    max_lag = min(round(PROXY_RATE * max_shift_seconds), min(len(a), len(b)) - 2)
    n = 1 << (len(a) + len(b) - 1).bit_length()
    correlation = np.fft.irfft(np.fft.rfft(a, n=n) * np.conj(np.fft.rfft(b, n=n)), n=n)
    lags = np.arange(-max_lag, max_lag + 1)
    # FFT cross correlation uses a[t+lag] against b[t].
    starts_a, starts_b = np.maximum(lags, 0), np.maximum(-lags, 0)
    lengths = np.minimum(len(a) - starts_a, len(b) - starts_b)
    usable = lengths > min(len(a), len(b)) * .9
    sum_a = np.r_[0., np.cumsum(a.astype(np.float64)**2)]
    sum_b = np.r_[0., np.cumsum(b.astype(np.float64)**2)]
    energies = np.sqrt((sum_a[starts_a + lengths] - sum_a[starts_a]) * (sum_b[starts_b + lengths] - sum_b[starts_b]))
    scores = np.where(usable, correlation[lags % n] / np.maximum(energies, 1e-12), 0.)
    best = int(np.argmax(np.abs(scores)))
    return {"correlation": float(np.clip(abs(scores[best]), 0, 1)),
            "polarity": 1 if scores[best] >= 0 else -1,
            "lag_seconds": float(lags[best] / PROXY_RATE),
            "overlap_fraction": float(lengths[best] / max(len(a), len(b)))}


@dataclass
class Candidate:
    artifact_id: str
    duration: float
    signature: np.ndarray
    path: Path


class NearDuplicateIndex:
    def __init__(self, workspace, max_candidates=16):
        self.root = Path(workspace) / "duplicate-proxies"
        self.root.mkdir()
        self.items = []
        self.max_candidates = max_candidates

    def add(self, artifact_id, core, sr):
        proxy = _proxy(core, sr)
        signature, variation = _signature(proxy)
        duration = len(core) / sr
        report = {"version": DUPLICATE_VERSION, "status": "checked", "candidate_count": 0,
                  "compared_count": 0, "shortlist_limited": False, "envelope_variation": variation,
                  "matches": [], "scope": "this_preparation_job"}
        if variation < .08:
            # Sustained pure/simple signals do not provide enough evidence of
            # the same recording. Exact hashes are checked separately.
            report["status"] = "inconclusive_low_variation"
            return report
        candidates = [c for c in self.items if abs(c.duration - duration) <= .12]
        report["candidate_count"] = len(candidates)
        report["shortlist_limited"] = len(candidates) > self.max_candidates
        candidates.sort(key=lambda c: (-float(np.dot(signature, c.signature)), c.artifact_id))
        for candidate in candidates[:self.max_candidates]:
            previous = np.load(candidate.path, allow_pickle=False)
            evidence = waveform_match(proxy, previous)
            report["compared_count"] += 1
            if evidence["correlation"] >= .992 and evidence["overlap_fraction"] >= .97:
                report["matches"].append({"artifact_id": candidate.artifact_id, **evidence})
        path = self.root / f"{len(self.items):06d}.npy"
        np.save(path, proxy, allow_pickle=False)
        self.items.append(Candidate(artifact_id, duration, signature, path))
        return report

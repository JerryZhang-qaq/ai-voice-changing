"""Pause-led slicing with optional singing evidence and retained boundary context."""
from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class SliceConfig:
    min_seconds: float = 2.0
    target_seconds: float = 8.0
    max_seconds: float = 15.0
    silence_seconds: float = 0.3
    padding_seconds: float = 0.15
    threshold_db: float | None = None

    def validate(self):
        if not 0.5 <= self.min_seconds <= self.target_seconds <= self.max_seconds <= 30:
            raise ValueError("切片长度需满足 0.5 ≤ 最短 ≤ 目标 ≤ 最长 ≤ 30 秒")
        if not 0.05 <= self.silence_seconds <= 2 or not 0 <= self.padding_seconds <= 0.5:
            raise ValueError("停顿或边缘余量超出允许范围")
        if self.threshold_db is not None and not -80 <= self.threshold_db <= -20:
            raise ValueError("活动阈值需在 -80 到 -20 dBFS 之间")

    def dict(self):
        return asdict(self)


def frame_energy(x, sr):
    hop, window = max(1, round(sr * .01)), max(1, round(sr * .02))
    starts = np.arange(0, len(x), hop, dtype=np.int64)
    # Prefix sums avoid an enormous overlapping frame matrix for long files.
    sums = np.concatenate(([0.], np.cumsum(np.square(x, dtype=np.float64))))
    ends = np.minimum(starts + window, len(x))
    power = (sums[ends] - sums[starts]) / (ends - starts)
    return starts, 10 * np.log10(np.maximum(power, 1e-12)), hop


def segment(x: np.ndarray, sr: int, config=SliceConfig(), *, voice=None):
    config.validate()
    if not len(x) or not np.isfinite(x).all():
        raise ValueError("切片输入为空或包含无效采样")
    starts, db, hop = frame_energy(x, sr)
    threshold = config.threshold_db
    if threshold is None:
        # Cap relative threshold to retain quiet singing. This is not a noise classifier.
        threshold = float(np.clip(np.percentile(db, 95) - 35, -60, -40))
    # Hysteresis: lower threshold only continues previously active runs.
    active = np.zeros(len(db), dtype=bool)
    on = False
    for i, value in enumerate(db):
        on = value >= (threshold - 4 if on else threshold)
        active[i] = on
    if not active.any():
        return [], {"threshold_db": threshold, "activity_seconds": 0.0}
    changes = np.diff(np.r_[False, active, False].astype(int))
    runs = list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))
    merged = []
    for start, end in runs:
        if merged and (start - merged[-1][1]) * hop / sr < config.silence_seconds:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    pad = round(config.padding_seconds * sr)
    core_limit = max(.2, config.max_seconds - 2 * config.padding_seconds)
    core_min = max(.2, config.min_seconds - 2 * config.padding_seconds)
    core_target = min(core_limit, max(core_min, config.target_seconds - 2 * config.padding_seconds))
    pieces = []
    for a, b in merged:
        core_start, core_end = int(starts[a]), min(len(x), int(starts[b - 1]) + hop + round(.02 * sr))
        cursor, left_forced = core_start, False
        while core_end - cursor > core_limit * sr:
            # Do not leave an unusably short tail merely to hit a fixed target.
            desired = core_target if core_end - cursor - core_target * sr >= core_min * sr else (core_end - cursor) / (2 * sr)
            target = cursor + round(desired * sr)
            lo = cursor + round(max(core_min, desired - 1.5) * sr)
            hi = min(cursor + round(core_limit * sr), target + round(1.5 * sr), core_end - round(core_min * sr))
            candidates = np.flatnonzero((starts >= lo) & (starts <= hi))
            if not len(candidates):
                cut = min(core_end, cursor + round(core_limit * sr))
                pieces.append((cursor, cut, True))
                cursor, left_forced = cut, True
                continue
            # Prefer low energy near the target; crossing an active region remains review-only.
            costs = db[candidates] + 3 * np.abs(starts[candidates] - target) / sr
            if voice is not None:
                times = starts[candidates] / sr
                confidence = np.interp(times, voice.times, voice.confidence)
                flux = np.interp(times, voice.times, voice.flux)
                # Prefer pauses and low confidence valleys. Unvoiced attacks
                # still carry a flux penalty, so consonants are not "silence".
                costs += 12 * np.clip(confidence, 0, 1) + 8 * np.clip(flux, 0, 2)
            idx = int(candidates[np.argmin(costs)])
            cut = int(starts[idx])
            forced = bool(db[idx] >= threshold)
            pieces.append((cursor, cut, left_forced or forced))
            cursor, left_forced = cut, forced
        pieces.append((cursor, core_end, left_forced))
    clips = []
    for a, b, forced in pieces:
        start, end = max(0, a - pad), min(len(x), b + pad)
        reasons = []
        if (b - a) / sr < config.min_seconds:
            reasons.append("SHORT_CLIP")
        if forced:
            reasons.append("UNSAFE_BOUNDARY")
        clips.append({"start_sample": start, "end_sample": end, "valid_start_sample": a, "valid_end_sample": b,
                      "duration": (end - start) / sr, "reasons": reasons})
    return clips, {"threshold_db": threshold, "activity_seconds": float(np.sum(active) * hop / sr),
                   "boundary_analysis": "pitch-and-flux" if voice is not None else "energy",
                   "unsafe_boundary_count": sum("UNSAFE_BOUNDARY" in c["reasons"] for c in clips)}

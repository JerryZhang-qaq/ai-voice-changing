"""Reference-free preservation guard, not a vocal-purity or perceptual-quality score.

Only same-content vocal cleanup may fall back to its input. A full song must never
be restored as a training vocal because extraction changed its spectrum/energy.
Thresholds are deliberately provisional until real singing calibration exists.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

from voice_workbench_audio import decode


GUARD_VERSION = "signal-preservation-1"
SAME_CONTENT_TASKS = {"dereverb", "lead_backing", "denoise"}


@dataclass
class SignalFeatures:
    rms_db: np.ndarray
    bands_db: np.ndarray
    duration: float
    peak: float
    clipping_ratio: float
    sample_rate: int
    channel: dict


def _features(blocks, sr, channel):
    frame = round(sr * .02)
    window = np.hanning(frame)
    frequencies = np.fft.rfftfreq(frame, 1 / sr)
    masks = [(frequencies >= low) & (frequencies < high)
             for low, high in ((50, 400), (400, 3000), (3000, 8001))]
    rms, bands, peak, clipped, count = [], [], 0., 0, 0
    for block in blocks:
        if not np.isfinite(block).all():
            raise ValueError("质量分析输入包含非有限采样值")
        if not len(block):
            continue
        count += len(block)
        peak = max(peak, float(np.max(np.abs(block))))
        clipped += int(np.count_nonzero(np.abs(block) >= .999))
        tail = len(block) % frame
        padded = np.pad(block, (0, frame - tail)) if tail else block
        framed = padded.reshape(-1, frame)
        sizes = np.full(len(framed), frame)
        if tail:
            sizes[-1] = tail
        rms.append(10 * np.log10(np.maximum(np.sum(framed.astype(np.float64)**2, axis=1) / sizes, 1e-14)))
        spectrum = np.abs(np.fft.rfft(framed * window, axis=1))**2
        bands.append(np.column_stack([10 * np.log10(np.maximum(spectrum[:, mask].sum(axis=1), 1e-14)) for mask in masks]))
    if not count:
        raise ValueError("质量分析输入为空")
    return SignalFeatures(np.concatenate(rms), np.concatenate(bands), count / sr,
                          peak, clipped / count, sr, channel)


def features_array(x, sr):
    """Useful for synthetic guard validation; no audio modifications are returned."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1:
        raise ValueError("质量分析需要单声道")
    return _features((x[i:i + sr] for i in range(0, len(x), sr)), sr, {"strategy": "mono"})


def features_file(path, workspace, progress=None):
    proxy = Path(workspace) / "quality-proxy.wav"
    decode(Path(path), proxy, target_rate=16000)
    with sf.SoundFile(proxy) as stream:
        sr, channels = stream.samplerate, stream.channels
        channel = {"strategy": "mono"}
        if channels == 2:
            # Choose once for the whole file, so channel choices cannot jump at
            # block boundaries. Mean subtraction handles DC correlation bias.
            sums, squares, cross, count = np.zeros(2), np.zeros(2), 0., 0
            for block in stream.blocks(blocksize=sr, dtype="float32", always_2d=True):
                if progress:
                    progress()
                if not np.isfinite(block).all():
                    raise ValueError("质量分析输入包含非有限采样值")
                sampled = block[::10].astype(np.float64)
                sums += sampled.sum(axis=0)
                squares += (sampled**2).sum(axis=0)
                cross += float((sampled[:, 0] * sampled[:, 1]).sum())
                count += len(sampled)
            variance = squares / count - (sums / count)**2
            denominator = float(np.sqrt(max(0, variance[0] * variance[1])))
            corr = (cross / count - np.prod(sums / count)) / denominator if denominator > 1e-12 else 0.
            channel = {"strategy": "single_channel", "channel": int(np.argmax(variance)), "correlation": float(corr)} if corr < -.2 else {"strategy": "mean", "correlation": float(corr)}
            stream.seek(0)
        def blocks():
            for block in stream.blocks(blocksize=sr, dtype="float32", always_2d=True):
                if progress:
                    progress()
                if channel["strategy"] == "single_channel":
                    yield block[:, channel["channel"]]
                else:
                    yield block.mean(axis=1, dtype=np.float32)
        return _features(blocks(), sr, channel)


def _envelope_delay(before, after):
    x, y = np.clip(before, -70, -10), np.clip(after, -70, -10)
    if len(x) < 50 or np.std(x) < 1 or np.std(y) < 1:
        return {"status": "insufficient_variation"}
    scores = {}
    for lag in range(-10, 11):
        a, b = (x[-lag:], y[:lag]) if lag < 0 else (x[:-lag], y[lag:]) if lag > 0 else (x, y)
        a, b = a - a.mean(), b - b.mean()
        denominator = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
        scores[lag] = float(np.dot(a, b) / denominator) if denominator > 1e-8 else 0.
    best = max(scores, key=scores.get)
    return {"status": "estimated", "lag_seconds": best * .02, "correlation": scores[best], "zero_lag_correlation": scores[0]}


def compare_features(before: SignalFeatures, after: SignalFeatures, task):
    comparable = task in SAME_CONTENT_TASKS
    report = {"version": GUARD_VERSION, "task": task, "calibrated": False,
              "mode": "same_vocal" if comparable else "changed_content",
              "decision": "candidate", "reasons": ["CLEANUP_COMPARISON_UNCALIBRATED"],
              "metrics": {"duration_delta_seconds": after.duration - before.duration,
                          "input_peak": before.peak, "output_peak": after.peak,
                          "input_clipping_ratio": before.clipping_ratio, "output_clipping_ratio": after.clipping_ratio}}
    reasons, metrics = report["reasons"], report["metrics"]
    if abs(metrics["duration_delta_seconds"]) > .06:
        reasons.append("CLEANUP_DURATION_CHANGED")
        if comparable:
            report["decision"] = "fallback_input"
    if after.clipping_ratio > max(.001, before.clipping_ratio + .0005) or after.peak > max(1., before.peak + .05):
        reasons.append("NEW_CLIPPING")
    if not comparable:
        # Intentional backing/instrument removal invalidates spectral/energy
        # preservation comparisons. Log levels without a fake damage score.
        reasons.append("EXTRACTION_CONTENT_CHANGED")
        return report
    count = min(len(before.rms_db), len(after.rms_db))
    original, output = before.rms_db[:count], after.rms_db[:count]
    threshold = float(np.clip(np.percentile(original, 95) - 35, -60, -40))
    active = original >= threshold
    metrics.update(input_activity_threshold_db=threshold, input_activity_seconds=float(active.sum() * .02))
    if active.sum() < 20:
        reasons.append("INPUT_ACTIVITY_UNCERTAIN")
        return report
    delta = output[active] - original[active]
    gain = float(np.median(delta))
    lost = delta - gain < -18
    metrics.update(median_level_change_db=gain, relative_activity_loss_fraction=float(lost.mean()))
    if gain < -24 and np.mean(delta < -24) > .75:
        reasons.append("CLEANUP_SIGNAL_COLLAPSE")
        report["decision"] = "fallback_input"
    if lost.mean() > .15:
        reasons.append("POSSIBLE_ACTIVITY_DAMAGE")
    # The main activity threshold cannot decide whether breath/quiet singing
    # survived. Monitor a lower band separately; a flag stays review-only since
    # reference-free analysis cannot distinguish it from intentionally removed
    # background noise.
    weak = (original >= max(-75., threshold - 20)) & (original < np.percentile(original[active], 90) - 15)
    if weak.sum() >= 5:
        weak_loss = float(np.mean(output[weak] - original[weak] - gain < -18))
        metrics["weak_activity_loss_fraction"] = weak_loss
        metrics["weak_activity_seconds"] = float(weak.sum() * .02)
        if weak_loss > .2:
            reasons.append("WEAK_ACTIVITY_LOSS")
    # Band ratios cancel FFT/sample-rate scale; gain compensation prevents a
    # harmless level adjustment being misreported as high-frequency damage.
    high_before = before.bands_db[:count, 2] - before.bands_db[:count, 1]
    high_after = after.bands_db[:count, 2] - after.bands_db[:count, 1]
    usable_high = active & (high_before > -20)
    if usable_high.sum() >= 10:
        loss_fraction = float(np.mean(high_after[usable_high] - high_before[usable_high] < -15))
        metrics["high_band_ratio_loss_fraction"] = loss_fraction
        if loss_fraction > .4:
            reasons.append("POSSIBLE_HIGH_FREQUENCY_DAMAGE")
    delay = _envelope_delay(original, output - gain)
    metrics["envelope_alignment"] = delay
    if delay["status"] == "estimated" and abs(delay["lag_seconds"]) > .06 and delay["correlation"] > .85 and delay["correlation"] - delay["zero_lag_correlation"] > .05:
        reasons.append("CLEANUP_TIMING_SHIFT")
        report["decision"] = "fallback_input"
    if report["decision"] == "fallback_input":
        reasons.append("CLEANUP_FALLBACK_REQUIRES_REVIEW")
    return report


def guard_cleaning(store, job_id, input_id, candidate_id, task, progress=None):
    """Persist the comparison and choose an input; both alternatives stay cached."""
    store.hold_inputs(job_id, [input_id, candidate_id])
    source, candidate = store.get(input_id), store.get(candidate_id)
    key = hashlib.sha256(json.dumps({"input": input_id, "input_sha": source["sha256"],
                                    "candidate": candidate_id, "candidate_sha": candidate["sha256"],
                                    "version": GUARD_VERSION, "task": task}, sort_keys=True).encode()).hexdigest()
    previous = store.cached(key, job_id)
    if previous:
        result = json.loads(store.path(previous["id"]).read_text())
        store.hold_inputs(job_id, [input_id, candidate_id])
        return {**result, "report_id": previous["id"]}
    with tempfile.TemporaryDirectory(dir=store.root, prefix=f"processing-{job_id}-") as temp:
        before = features_file(store.path(input_id), temp, progress)
        try:
            after = features_file(store.path(candidate_id), temp, progress)
        except ValueError as error:
            # Invalid output is never silently used, and a song fallback must
            # not masquerade as a usable extracted vocal.
            if task not in SAME_CONTENT_TASKS:
                raise ValueError("人声提取结果无效，不能回退到完整歌曲") from error
            result = {"version": GUARD_VERSION, "task": task, "calibrated": False,
                      "mode": "same_vocal", "decision": "fallback_input", "metrics": {},
                      "reasons": ["INVALID_CLEANUP_OUTPUT", "CLEANUP_FALLBACK_REQUIRES_REVIEW"]}
        else:
            result = compare_features(before, after, task)
        result.update(input_id=input_id, candidate_id=candidate_id,
                      input_sha256=source["sha256"], candidate_sha256=candidate["sha256"],
                      selected_id=input_id if result["decision"] == "fallback_input" else candidate_id)
        path = Path(temp) / "comparison.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact = store.import_file(path, name="清洗前后对比.json", job_id=job_id,
                                     metadata={"kind": "cleanup_comparison", "cache_key": key, "guard_version": GUARD_VERSION})
        return {**result, "report_id": artifact["id"]}

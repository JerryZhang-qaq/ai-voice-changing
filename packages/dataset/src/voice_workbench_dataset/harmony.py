"""Conservative overlap gate using a mature lead/backing separator's stems.

Complex overlaps reject the entire source. Model leakage and octave ambiguity
remain review cases; there is no singer recognition or voice clustering.
"""
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np

from .analysis import analyze_file


HARMONY_VERSION = "lead-backing-overlap-1"


def _longest(mask):
    changes = np.diff(np.r_[False, mask, False].astype(np.int8))
    lengths = np.flatnonzero(changes == -1) - np.flatnonzero(changes == 1)
    return float(lengths.max() * .02) if len(lengths) else 0.


def assess_harmony(lead, backing):
    result = {"version": HARMONY_VERSION, "status": "review", "reasons": [],
              "calibrated_on_real_harmonies": False, "metrics": {}}
    if abs(lead.duration - backing.duration) > .06:
        result["reasons"] = ["HARMONY_STEMS_MISALIGNED"]
        return result
    n = min(len(lead.times), len(backing.times))
    lr, br = lead.rms_db[:n], backing.rms_db[:n]
    lf, bf = lead.f0[:n], backing.f0[:n]
    loudness_gate = float(np.clip(np.percentile(lr, 95) - 40, -65, -40))
    active = lr >= loudness_gate
    vocal = active & (lead.confidence[:n] >= .65) & (lf > 0)
    if vocal.sum() < 25:
        result["reasons"] = ["HARMONY_INPUT_UNCERTAIN"]
        return result
    relative_db = br - lr
    simultaneous = vocal & (backing.confidence[:n] >= .7) & (bf > 0) & (relative_db >= -22) & (br > -70)
    cents = 1200 * np.log2(np.maximum(bf, 1) / np.maximum(lf, 1))
    # Correlated lead leakage often differs by an octave due to pitch-tracker
    # ambiguity. Those cases are not enough to assert a complex harmony.
    octave_distance = np.abs(cents - 1200 * np.round(cents / 1200))
    distinct = simultaneous & (octave_distance > 140)
    independent_seconds = float(distinct.sum() * .02)
    fraction = float(distinct.sum() / max(1, vocal.sum()))
    longest = _longest(distinct)
    significant_backing = active & (relative_db > -16) & (br > -65)
    result["metrics"] = {"lead_voiced_seconds": float(vocal.sum() * .02),
                         "independent_overlap_seconds": independent_seconds,
                         "independent_overlap_fraction": fraction, "longest_overlap_seconds": longest,
                         "significant_backing_seconds": float(significant_backing.sum() * .02),
                         "median_backing_to_lead_db": float(np.median(relative_db[active]))}
    if (independent_seconds >= .8 and fraction >= .08) or longest >= 1.2:
        result.update(status="rejected", reasons=["COMPLEX_HARMONY"])
    elif distinct.sum() >= 10 or (significant_backing.sum() >= 40 and significant_backing.mean() >= .1):
        result["reasons"] = ["HARMONY_UNCERTAIN"]
    else:
        result.update(status="passed", reasons=[])
    # Preserve compact intervals for audition, rather than dumping frame arrays.
    changes = np.diff(np.r_[False, distinct, False].astype(np.int8))
    result["intervals"] = [{"start": float(a * .02), "end": float(b * .02)} for a, b in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)) if b - a >= 5][:100]
    return result


def check_harmony(store, job_id, source_id, stems, progress=None):
    key_data = {"version": HARMONY_VERSION, "source": source_id,
                "stems": [(name, aid, store.get(aid)["sha256"]) for name, aid in sorted(stems.items())]}
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
    store.hold_inputs(job_id, [source_id, stems["lead"], stems["backing"]])
    cached = store.cached(key, job_id)
    if cached:
        return {**json.loads(store.path(cached["id"]).read_text()), "report_id": cached["id"]}
    with tempfile.TemporaryDirectory(dir=store.root, prefix=f"processing-{job_id}-") as temporary:
        lead, _ = analyze_file(store.path(stems["lead"]), temporary, progress)
        backing, _ = analyze_file(store.path(stems["backing"]), temporary, progress)
        result = assess_harmony(lead, backing)
        result.update(source_id=source_id, lead_id=stems["lead"], backing_id=stems["backing"],
                      engine=store.get(stems["lead"])["metadata"])
        path = Path(temporary) / "harmony.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact = store.import_file(path, name="独唱和声检查.json", job_id=job_id,
                                     metadata={"kind": "harmony_report", "cache_key": key})
        return {**result, "report_id": artifact["id"]}

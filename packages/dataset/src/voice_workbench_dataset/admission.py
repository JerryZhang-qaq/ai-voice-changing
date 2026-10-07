"""Clip-level decisions; harmony warnings always allow human review."""
from dataclasses import asdict, dataclass

import numpy as np


ADMISSION_VERSION = "clip-review-alpha-3"


@dataclass(frozen=True)
class PreparationPolicy:
    mode: str = "review"
    solo_confirmed: bool = False

    def validate(self):
        if self.mode not in {"review", "automatic"}:
            raise ValueError("数据准备模式应为 review 或 automatic")
        if self.mode == "automatic" and not self.solo_confirmed:
            raise ValueError("自动准备前需确认所有素材均属于同一歌手")

    def dict(self):
        return asdict(self)


def plateau_ratio(audio):
    if len(audio) < 3:
        return 0.
    flat = (np.abs(audio[1:-1]) >= .999) & (np.abs(audio[1:-1] - audio[:-2]) < 1e-6) & (np.abs(audio[1:-1] - audio[2:]) < 1e-6)
    return float(flat.mean())


def decide_clip(policy, metrics, reasons, voice, harmony):
    reasons = list(dict.fromkeys(reasons))
    evidence = {"version": ADMISSION_VERSION, "mode": policy.mode, "solo_confirmed": policy.solo_confirmed,
                "identity_check": "out_of_scope", "threshold_validation": "alpha", "harmony_status": harmony.get("status", "not_checked")}
    excluded = {"EXACT_DUPLICATE", "LOW_SIGNAL", "SEVERE_CLIPPING", "CLIP_UNDER_ONE_SECOND"}
    if metrics.get("duration", 1) < 1:
        reasons.append("CLIP_UNDER_ONE_SECOND")
    if metrics["rms_db"] < -70:
        reasons.append("LOW_SIGNAL")
    if metrics.get("plateau_ratio", 0) > .01:
        reasons.append("SEVERE_CLIPPING")
    if metrics["clipping_ratio"] > .001 or metrics["peak"] > 1:
        reasons.append("CLIPPING_DETECTED")
    if voice["voiced_seconds"] < .3:
        reasons.append("VOCAL_CONTENT_UNCERTAIN")
    if harmony.get("status") == "rejected":
        reasons.append("COMPLEX_HARMONY")
    elif harmony.get("status") != "passed":
        reasons.extend(harmony.get("reasons") or ["HARMONY_NOT_CHECKED"])
    if not policy.solo_confirmed:
        reasons.append("SOLO_DECLARATION_REQUIRED")
    reasons = list(dict.fromkeys(reasons))
    if excluded.intersection(reasons):
        status = "excluded"
    else:
        informational = {"CLEANUP_COMPARISON_UNCALIBRATED", "EXTRACTION_CONTENT_CHANGED"}
        risks = [reason for reason in reasons if reason not in informational]
        status = "accepted" if policy.mode == "automatic" and not risks else "review"
    evidence["origin"] = "automatic" if status == "accepted" else "rules"
    return status, reasons, evidence

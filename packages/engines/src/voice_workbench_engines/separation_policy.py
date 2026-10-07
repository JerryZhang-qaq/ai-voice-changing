"""Explicit, versioned speed/quality tradeoffs for the pinned RoFormer models."""

POLICY_VERSION = "roformer-speed-1"
PROFILES = {"quality": ("fp32", 8), "balanced": ("amp_fp16", 4), "fast": ("amp_fp16", 2)}
MODEL_OVERLAPS = {"vocals_melband_unwa": 8, "lead_melband_aufr33": 4,
                  "dereverb_melband_anvuew": 2, "denoise_melband_aufr33": 4}


def parameters(model_id, profile="balanced", segment_size=256, overlap=None):
    if profile not in PROFILES:
        raise ValueError("分离模式应为 quality、balanced 或 fast")
    precision, default_overlap = PROFILES[profile]
    if overlap is None:
        overlap = default_overlap if profile == "quality" else min(default_overlap, MODEL_OVERLAPS[model_id])
    if not 64 <= segment_size <= 512 or not 2 <= overlap <= 50:
        raise ValueError("分离窗口应为 64—512，重叠应为 2—50")
    return {"profile": profile, "precision": precision, "attention": "legacy" if profile == "quality" else "auto_sdpa",
            "segment_size": segment_size, "overlap": overlap, "policy_version": POLICY_VERSION}

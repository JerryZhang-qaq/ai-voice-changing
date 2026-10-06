"""Curated upstream identities, not a claim of measured quality on this machine."""
from pathlib import Path
import os


SEPARATION_MODELS = {
    "vocals_melband_unwa": {
        "name": "MelBand RoFormer · unwa FT3", "task": "vocals",
        "filename": "mel_band_roformer_kim_ft3_unwa.ckpt",
        "config": "config_mel_band_roformer_kim_ft_unwa.yaml",
        "stems": {"vocals": "vocals", "other": "instrumental"},
        "evidence": "audio-separator 官方模型目录收录；本项目尚未做听感验收",
    },
    "lead_melband_aufr33": {
        "name": "MelBand RoFormer · aufr33/viperx Karaoke", "task": "lead_backing",
        "filename": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
        "config": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956_config.yaml",
        "stems": {"Vocals": "lead", "Instrumental": "backing"},
        "evidence": "上游 Karaoke 模型；仅对已分离的人声运行，须试听验证主唱/和声效果",
    },
    "dereverb_melband_anvuew": {
        "name": "MelBand RoFormer · anvuew 温和去混响", "task": "dereverb",
        "filename": "dereverb_mel_band_roformer_less_aggressive_anvuew_sdr_18.8050.ckpt",
        "config": "dereverb_mel_band_roformer_anvuew.yaml",
        "stems": {"noreverb": "dry", "reverb": "reverb"},
        "evidence": "audio-separator 官方模型目录收录；启用前后需比较处理损伤",
    },
    "denoise_melband_aufr33": {
        "name": "MelBand RoFormer · aufr33 降噪", "task": "denoise",
        "filename": "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
        "config": "denoise_mel_band_roformer_aufr33_sdr_27.9959_config.yaml",
        "stems": {"dry": "clean", "other": "noise"},
        "evidence": "audio-separator 官方目录中的降噪模型；仅按需启用，并执行处理损伤检查",
    },
}

RVC_REVISION = "81eed5e8f68b6bed1789f682fe78cdd324495afc"


def settings():
    runtime = Path(os.environ.get("WORKBENCH_RUNTIME", "runtime")).resolve()
    def python(engine, variable):
        relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        candidate = runtime / "venvs" / engine / relative
        return os.environ.get(variable, str(candidate) if candidate.is_file() else "")
    return {"separation_python": python("separation", "SEPARATION_PYTHON"),
            "separation_models": str(Path(os.environ.get("SEPARATION_MODELS", runtime / "models" / "separation")).resolve()),
            "rvc_python": python("rvc", "RVC_PYTHON"),
            "rvc_root": str(Path(os.environ.get("RVC_ROOT", runtime / "engines" / "rvc")).resolve())}

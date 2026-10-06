"""Model parsing stays outside the API and uses PyTorch's restricted loader."""
import json
from pathlib import Path
import sys


def main():
    import torch
    model = torch.load(sys.argv[1], map_location="cpu", weights_only=True)
    if not isinstance(model, dict) or not isinstance(model.get("weight"), dict):
        raise ValueError("不是 RVC 推理模型")
    embedding = model["weight"].get("emb_g.weight")
    config = model.get("config")
    version = model.get("version", "v1")
    if embedding is None or embedding.ndim != 2 or not isinstance(config, list) or len(config) < 18 or version not in {"v1", "v2"}:
        raise ValueError("模型结构不兼容")
    sr = int(config[-1])
    if sr not in {32000, 40000, 48000}:
        raise ValueError("模型采样率不支持")
    result = {"kind": "rvc_model", "version": version, "sample_rate": sr, "f0": bool(model.get("f0", 1)), "speaker_count": int(embedding.shape[0]), "speaker_id": 0}
    if len(sys.argv) > 3:
        import faiss
        index = faiss.read_index(sys.argv[3])
        if index.d != (768 if version == "v2" else 256) or index.ntotal <= 0:
            raise ValueError("索引维度与模型不兼容或索引为空")
        result["index_dimension"] = index.d
    Path(sys.argv[2]).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()

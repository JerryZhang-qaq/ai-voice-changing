"""Real RoFormer execution on paired Chinese and Japanese recordings.

CPU mode is an explicit benchmark exception; product GPU requirements stay on.
Run with the independent audio-separator interpreter and package source paths.
"""
import argparse
import gc
import json
from pathlib import Path
import time

import numpy as np
import soundfile as sf
import torch

from voice_workbench_audio import decode, mono
from voice_workbench_engines.registry import SEPARATION_MODELS
from voice_workbench_engines.separation_bridge import build_separator
from voice_workbench_engines.resources import catalog, hash_file
from voice_workbench_dataset.analysis import analyze_file
from voice_workbench_dataset.harmony import assess_harmony
from voice_workbench_dataset.quality import features_file, compare_features


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--overlap-only", action="store_true")
    args = parser.parse_args()
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    report = {"device":"cuda" if torch.cuda.is_available() else "cpu", "torch":torch.__version__,
              "audio_separator":"0.47.0", "segment_size":128, "overlap":2,
              "limitations":["短片段执行检查，不代表整套歌曲质量", "参数低于产品默认重叠，未进行盲听或阈值校准"], "runs":[]}
    if args.overlap_only:
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    for resource in catalog():
        if resource["id"] not in SEPARATION_MODELS:
            continue
        if args.overlap_only and resource["id"] != "lead_melband_aufr33":
            continue
        for entry in resource["files"]:
            path = args.model_dir / entry["path"]
            if not path.is_file() or path.stat().st_size != entry["size"] or hash_file(path) != entry["sha256"]:
                raise RuntimeError(f"权重校验失败：{path}")
    def run(mid, source, folder):
        folder.mkdir(parents=True, exist_ok=True)
        spec = SEPARATION_MODELS[mid]
        request = {"model_dir":str(args.model_dir.resolve()),"output_dir":str(folder),"filename":spec["filename"],"config":spec["config"],"stems":spec["stems"],"segment_size":128,"overlap":2}
        start = time.monotonic()
        separator = build_separator(request)
        names = separator.separate(str(source), custom_output_names=spec["stems"])
        result = {Path(n).stem:folder/n for n in names}
        stats = {"model_id":mid,"seconds":time.monotonic()-start,"input":str(source),"outputs":{k:{"duration":sf.info(v).duration,"frames":sf.info(v).frames,"sample_rate":sf.info(v).samplerate} for k,v in result.items()}}
        if any(abs(sf.info(v).duration-sf.info(source).duration)>.05 for v in result.values()):
            raise RuntimeError("真实分离改变了输入时长")
        report["runs"].append(stats)
        (output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        del separator
        gc.collect()
        print(mid, round(stats["seconds"],2), flush=True)
        return result, stats
    for language, name in [("cn", "cn/Sample1_Mixed.wav"), ("jp", "jp-f001/nitech_jp_song070_f001_007.wav")]:
        if args.overlap_only:
            break
        folder = output/language
        folder.mkdir(exist_ok=True)
        x,sr=sf.read(args.input_root/name,dtype="float32",always_2d=True)
        start_seconds = 10 if language=="cn" else 3
        excerpt = folder/"excerpt.wav"
        sf.write(excerpt,x[round(start_seconds*sr):round((start_seconds+8)*sr)],sr,subtype="FLOAT")
        master = folder/"master.wav"
        decode(excerpt,master,target_rate=44100)
        vocal = master
        if language=="cn":
            stems,stats = run("vocals_melband_unwa",master,folder/"vocals")
            vocal=stems["vocals"]
            dry,sr=sf.read(args.input_root/'cn/sample1.wav',dtype="float32")
            reference=folder/"reference.wav"
            sf.write(reference,dry[round(start_seconds*sr):round((start_seconds+8)*sr)],sr,subtype="FLOAT")
            stats["paired_dry_comparison"]=compare_features(features_file(reference,folder),features_file(vocal,folder),"dereverb")
        stems,stats=run("lead_melband_aufr33",vocal,folder/"harmony")
        lead,_=analyze_file(stems["lead"],folder)
        backing,_=analyze_file(stems["backing"],folder)
        stats["harmony_screen"]=assess_harmony(lead,backing)
        for mid,stem,task in [("dereverb_melband_anvuew","dry","dereverb"),("denoise_melband_aufr33","clean","denoise")]:
            cleaned,stats=run(mid,vocal,folder/task)
            stats["preservation"]=compare_features(features_file(vocal,folder),features_file(cleaned[stem],folder),task)
    # Known two-person overlap made from two real recordings. It is distinct
    # from an independently annotated natural harmony evaluation set.
    overlap_dir = output / "controlled-overlap"
    overlap_dir.mkdir(exist_ok=True)
    cn = overlap_dir / "cn.wav"
    jp = overlap_dir / "jp.wav"
    decode(output / "cn/reference.wav", cn, target_rate=44100)
    decode(output / "jp/master.wav", jp, target_rate=44100)
    a,sr = sf.read(cn,dtype="float32",always_2d=True)
    b,_ = sf.read(jp,dtype="float32",always_2d=True)
    a,_ = mono(a)
    b,_ = mono(b)
    n = min(len(a),len(b))
    a = a[:n] / max(float(np.sqrt(np.mean(a[:n]**2))),1e-6)
    b = b[:n] / max(float(np.sqrt(np.mean(b[:n]**2))),1e-6)
    mixture = .1 * (a+b)
    mixture *= min(1., .8 / max(float(np.max(np.abs(mixture))),1e-6))
    path = overlap_dir / "two-real-singers.wav"
    sf.write(path,mixture,sr,subtype="FLOAT")
    stems,stats = run("lead_melband_aufr33",path,overlap_dir / "stems")
    lead,_ = analyze_file(stems["lead"],overlap_dir)
    backing,_ = analyze_file(stems["backing"],overlap_dir)
    stats["harmony_screen"] = assess_harmony(lead,backing)
    stats["known_label"] = "controlled_two_real_singers_overlap"
    (output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

import numpy as np
import soundfile as sf

from voice_workbench_storage import ArtifactStore
from .io import AudioError


def mix(store: ArtifactStore, job_id, vocal_id, instrumental_id, *, backing_id=None, backing_db=0., vocal_db=0., instrumental_db=0., format="wav", progress=None):
    if format not in {"wav", "flac", "mp3"}:
        raise ValueError("不支持的导出格式")
    with tempfile.TemporaryDirectory(dir=store.root, prefix=f"processing-{job_id}-") as tmp:
        work = Path(tmp)
        tracks = []
        from contextlib import ExitStack
        inputs = [(vocal_id, "vocal"), (instrumental_id, "instrumental")]
        if backing_id:
            inputs.append((backing_id, "backing"))
        for aid, name in inputs:
            if progress:
                progress()
            output = work / f"{name}.wav"
            result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(store.path(aid)), "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", "-y", str(output)], capture_output=True, timeout=180)
            if result.returncode:
                raise AudioError("混音音轨无法解码")
            tracks.append(output)
        sizes = [sf.info(track).frames for track in tracks]
        if min(sizes) <= 0 or max(sizes) - min(sizes) > 4410:
                raise AudioError("人声与伴奏时长相差超过 100 毫秒，请检查对齐")
        # Two passes allow one track-wide attenuation, rather than pumping a limiter.
        gains = [10 ** (vocal_db / 20), 10 ** (instrumental_db / 20)]
        if backing_id:
            gains.append(10 ** (backing_db / 20))
        peak = 0.
        frames = max(sf.info(t).frames for t in tracks)
        def blocks():
            with ExitStack() as stack:
                streams = [stack.enter_context(sf.SoundFile(track)) for track in tracks]
                for offset in range(0, frames, 65536):
                    n = min(65536, frames - offset)
                    output = np.zeros((n, 2), dtype="float32")
                    for stream, gain in zip(streams, gains):
                        x = stream.read(n, dtype="float32", always_2d=True)
                        if not np.isfinite(x).all():
                            raise AudioError("混音输入含无效采样")
                        output[:len(x)] += x * gain
                    yield output
        for block in blocks():
            peak = max(peak, float(np.max(np.abs(block))))
        attenuation = min(1., 10 ** (-1 / 20) / max(peak, 1e-12))
        master = work / "mix.wav"
        with sf.SoundFile(master, "w", samplerate=44100, channels=2, subtype="PCM_24") as out:
            for block in blocks():
                if progress:
                    progress()
                out.write(block * attenuation)
        output = work / f"cover.{format}"
        if format == "wav":
            master.rename(output)
        else:
            args = ["-c:a", "flac"] if format == "flac" else ["-c:a", "libmp3lame", "-b:a", "320k"]
            result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(master), *args, "-y", str(output)], capture_output=True, timeout=180)
            if result.returncode:
                raise AudioError("成曲编码失败")
        from voice_workbench_storage.store import readable_stem
        title = Path(store.get(vocal_id)["name"]).stem
        return store.import_file(output, name=f"{readable_stem(title,100)}-mix.{format}", role="export", job_id=job_id,
                                 metadata={"kind": "mixed_audio", "vocal_id": vocal_id, "instrumental_id": instrumental_id,
                                           "parameters": {"vocal_db": vocal_db, "instrumental_db": instrumental_db, "format": format},
                                           "backing_id": backing_id, "backing_db": backing_db,
                                           "peak_attenuation_db": float(20 * np.log10(attenuation))})["id"]

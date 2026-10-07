"""CPU orchestration substitutes; these are not CUDA performance measurements."""
from collections import namedtuple
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from voice_workbench_engines import separation_bridge as bridge
from voice_workbench_engines.separation_policy import parameters


@pytest.fixture
def runner(tmp_path, monkeypatch):
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: False)
    policy = ModuleType("audio_separator.separator.execution_policy")
    policy.AUTOCAST, policy.FP32 = "autocast", "fp32"
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "audio_separator.separator.execution_policy", policy)
    cfg = namedtuple("Config", "enable_flash enable_math enable_mem_efficient")
    attend_type = type("Attend", (), {"__module__": "audio_separator.separator.uvr_lib_v5.roformer.attend"})
    attend = attend_type()
    attend.flash, attend.cuda_config = True, cfg(False, True, True)
    instance = SimpleNamespace(model_run=SimpleNamespace(modules=lambda: [attend]))
    calls, loads = [], []
    errors = []

    class Separator:
        model_instance = instance
        workbench_inference_seconds = .25

        def separate(self, source, custom_output_names):
            calls.append((instance.effective_precision, instance.segment_size, instance.overlap))
            if errors:
                raise errors.pop(0)
            return ["vocals.wav"]

    def load(request):
        loads.append(request["filename"])
        return Separator()

    monkeypatch.setattr(bridge, "build_separator", load)
    request = {"input": "test.wav", "filename": "fixed.ckpt", "config": "fixed.yaml", "model_dir": str(tmp_path),
               "output_dir": str(tmp_path), "response": str(tmp_path / "response.json"),
               "stems": {"vocals": "vocals", "other": "instrumental"}, "keep_stems": ["vocals"],
               **parameters("vocals_melband_unwa")}
    return bridge.SeparatorRunner(), request, calls, loads, errors, attend


def test_resident_model_reused_and_profile_changes_precision_and_overlap(runner):
    engine, request, calls, loads, errors, attend = runner
    engine.run(request)
    assert attend.cuda_config.enable_flash and attend.cuda_config.enable_math
    engine.run({**request, **parameters("vocals_melband_unwa", "quality")})
    assert calls == [("autocast", 256, 4), ("fp32", 256, 8)]
    assert loads == ["fixed.ckpt"]
    assert json.loads(Path(request["response"]).read_text())["performance"]["model_reused"]
    assert not attend.cuda_config.enable_flash
    engine.run({**request, "filename": "different.ckpt"})
    assert loads == ["fixed.ckpt", "different.ckpt"]


def test_nan_retry_is_visible_and_records_actual_precision(runner):
    engine, request, calls, loads, errors, _ = runner
    errors.append(bridge.NonFiniteOutput("NaN in inference"))
    engine.run(request)
    result = json.loads(Path(request["response"]).read_text())
    assert calls == [("autocast", 256, 4), ("fp32", 256, 4)]
    assert result["parameters"]["precision"] == "fp32"
    assert result["performance"]["fallback_reason"] == "NaN in inference"


def test_oom_retries_smaller_window_and_other_errors_are_not_hidden(runner):
    engine, request, calls, loads, errors, _ = runner
    errors.append(RuntimeError("CUDA out of memory"))
    engine.run(request)
    assert calls == [("autocast", 256, 4), ("autocast", 128, 4)]
    assert json.loads(Path(request["response"]).read_text())["parameters"]["segment_size"] == 128
    errors.append(RuntimeError("input file is invalid"))
    with pytest.raises(RuntimeError, match="input file"):
        engine.run(request)
    assert len(calls) == 3


def test_finite_validation_happens_before_audio_export():
    bridge.check_finite({"vocals": np.zeros((2, 12), dtype=np.float32)})
    with pytest.raises(bridge.NonFiniteOutput):
        bridge.check_finite({"vocals": np.array([0., np.nan])})
    with pytest.raises(bridge.NonFiniteOutput):
        bridge.check_finite(np.array([np.inf]))


def test_profile_validation_and_model_specific_overlap():
    assert parameters("dereverb_melband_anvuew")["overlap"] == 2
    assert parameters("vocals_melband_unwa", "fast")["overlap"] == 2
    with pytest.raises(ValueError):
        parameters("vocals_melband_unwa", "invalid")
    with pytest.raises(ValueError):
        parameters("vocals_melband_unwa", segment_size=63)

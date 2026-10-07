"""Observe real log records and execute the instrumented global batch counter."""
import json
from types import SimpleNamespace

import pytest
from voice_workbench_engines.telemetry import LogTelemetry
from voice_workbench_engines.instrumentation.sitecustomize import instrument
from voice_workbench_storage import ArtifactStore


def test_incremental_utf8_progress_loss_and_checkpoint(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / 'runtime')
    job = store.create_job('rvc_train', status='running')
    log = tmp_path / 'engine.log'
    log.write_text('进度: 99/100\n', encoding='utf-8')
    telemetry = LogTelemetry(store, job['id'], log, 'RVC 训练')
    monkeypatch.setattr('voice_workbench_engines.telemetry.gpu_sample', lambda: None)
    event = {'event':'workbench_training','epoch':2,'epochs':5,'batch':3,'batches':4,'step':7,'loss_g':12.5,'loss_d':3.5}
    record = json.dumps(event).encode() + '\n保存模型和优化器 checkpoint.pth\n'.encode()
    with log.open('ab') as stream: stream.write(record[:-2])
    telemetry.tick()
    state = store.job(job['id'])['metadata']
    assert state['done'] == 1.75 and state['total'] == 5
    assert state['training']['step'] == 7 and state['training']['gpu'] is None
    assert state['loss_history'][-1]['loss_g'] == 12.5
    with log.open('ab') as stream: stream.write(record[-2:])
    telemetry.tick()
    assert 'checkpoint.pth' in store.job(job['id'])['metadata']['checkpoint']
    assert LogTelemetry.parse('67%|████| 2/3 [00:01]')['done'] == 2
    assert LogTelemetry.parse('[]') is None
    assert LogTelemetry.parse(json.dumps({**event,'loss_g':float('nan')})) is None


def test_instrumented_loop_preserves_global_step_and_observes_losses(capsys):
    source = '''
from __future__ import annotations
global_step = 0
def train(rank, epoch, hps, train_loader, loss_gen_all, loss_disc):
    global global_step
    seen = []
    for batch_idx in range(len(train_loader)):
        seen.append(batch_idx)
        global_step += 1
    return seen
'''
    class Loss:
        def __init__(self, value): self.value = value
        def detach(self): return self
        def item(self): return self.value
    namespace = {}
    exec(instrument(source, 'pinned-loop-fixture.py'), namespace)
    assert namespace['train'](0, 3, SimpleNamespace(total_epoch=5), [1,2,3], Loss(12), Loss(4)) == [0,1,2]
    assert namespace['global_step'] == 3
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]['step'] == records[-1]['batch'] == records[-1]['batches'] == 3
    assert records[-1]['loss_g'] == 12
    namespace['train'](1, 4, SimpleNamespace(total_epoch=5), [1], Loss(10), Loss(3))
    assert capsys.readouterr().out == '' and namespace['global_step'] == 4
    with pytest.raises(RuntimeError, match='固定版本'): instrument('x = 1', 'unsupported.py')

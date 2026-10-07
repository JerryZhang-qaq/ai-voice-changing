"""Explicit CPU browser fixture. GPU cores are substitutes, never production defaults.

Folder import, signal analysis, curation, exports, storage and mixing use real code.
Only separation, training and downloading are substituted for offline UI checks.
"""
import argparse
from io import BytesIO
from pathlib import Path
import threading
import time

import numpy as np
import soundfile as sf
import uvicorn

from voice_workbench_api.app import create_app
from voice_workbench_worker import runner
from voice_workbench_engines.registry import SEPARATION_MODELS

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', type=Path, required=True)
parser.add_argument('--web', type=Path, required=True)
parser.add_argument('--port', type=int, default=8123)
args = parser.parse_args()
app = create_app(args.runtime, args.web)
store = app.state.store
store.set_runtime('engines', {'rvc': {'ready':True,'environment':{'ready':True,'reason':'CPU browser fixture; no GPU claim'},'missing_base_files':[], 'training_rates':{'32k':True,'40k':True,'48k':True}}, 'separation':{'environment':{'ready':True,'reason':'CPU browser fixture'},'models':[{'id':key, **value,'ready':True} for key,value in SEPARATION_MODELS.items()]}})


def separation(store, job_id, source_id, model_id, progress=None, **kwargs):
    # Preserve true duration and samples so CPU boundary/harmony code still runs.
    x, sr = sf.read(store.path(source_id), dtype='float32', always_2d=True)
    stems = kwargs.get('keep_stems') or list(SEPARATION_MODELS[model_id]['stems'].values())
    for chunk in range(10):
        if progress: progress()
        store.update_job(job_id,'running',metadata={'engine_progress':{'state':'running','done':chunk,'total':10,'unit':'chunks','parameters':{'profile':kwargs.get('profile','balanced'),'precision':'fp32','overlap':4},'fixture':True}})
        time.sleep(.2)
    outputs = {}
    for i, stem in enumerate(stems):
        if progress: progress()
        time.sleep(.15)
        content = BytesIO()
        sf.write(content, x if stem not in {'instrumental','backing','noise','reverb'} else np.zeros_like(x), sr, format='WAV', subtype='FLOAT')
        name = f"{Path(store.get(source_id)['name']).stem}-{stem}.wav"
        outputs[stem] = store.import_stream(BytesIO(content.getvalue()), name=name, job_id=job_id, metadata={'kind':'separated_audio','stem':stem,'source_id':source_id,'fixture':True})['id']
    return outputs


def training(store, job_id, dataset_id, epochs=3, progress=None, **kwargs):
    if progress: progress('RVC 训练')
    for epoch in range(1, 4):
        for batch in range(1,5):
            if progress: progress('RVC 训练')
            store.update_job(job_id,'running',metadata={'done':epoch-1+batch/4,'total':3,'unit':'epochs','training':{'epoch':epoch,'epochs':3,'batch':batch,'batches':4,'step':(epoch-1)*4+batch,'loss_g':20-epoch,'loss_d':5-epoch/2,'gpu':None},'loss_history':[{'epoch':j,'loss_g':20-j,'loss_d':5-j/2} for j in range(1,epoch+1)]})
            time.sleep(.3)
    name=store.get(dataset_id)['metadata'].get('singer','fixture')
    model=store.import_stream(BytesIO(b'not-a-real-model'),name=f'{name}-fixture-v2-40k-e3-bs4-s12.pth',role='model',job_id=job_id,metadata={'kind':'rvc_model','singer':name,'sample_rate':40000,'fixture':True})
    return {'model':model['id']}


def resources(store, job_id, resource_ids, verify_only=False, progress=None, **kwargs):
    total=3*1024*1024
    for done in range(0,total+1,262144):
        if progress: progress('校验资源' if verify_only else '下载资源 · CPU UI fixture',done,total)
        time.sleep(.12)
    return None

runner.separate=separation
runner.train=training
import voice_workbench_engines.resources
voice_workbench_engines.resources.install_resources=resources


def worker():
    while True:
        if not runner.run_one(store): time.sleep(.05)
threading.Thread(target=worker, daemon=True).start()
uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')

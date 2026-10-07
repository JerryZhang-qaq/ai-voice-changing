from io import BytesIO
import json
import zipfile

from fastapi.testclient import TestClient
import numpy as np
import pytest
import soundfile as sf

from voice_workbench_api.app import create_app
from voice_workbench_dataset.admission import PreparationPolicy, decide_clip
from voice_workbench_storage import ArtifactStore, NotFound
from voice_workbench_worker.runner import run_one
from voice_workbench_audio.mix import mix


def wav(seconds=3, frequency=440):
    sr = 16000
    x = .15 * np.sin(2 * np.pi * frequency * np.arange(round(sr * seconds)) / sr)
    stream = BytesIO()
    sf.write(stream, x.astype('float32'), sr, format='WAV', subtype='FLOAT')
    return stream.getvalue()


def test_whole_folder_import_atomic_reuse_and_singer_paths(tmp_path):
    app = create_app(tmp_path)
    client, store = TestClient(app), app.state.store
    payload = [('files', ('first.wav', wav())), ('files', ('second.wav', wav(frequency=660)))]
    fields = {'singer': 'Aimer', 'kind': 'dry_vocal', 'paths': json.dumps(['Aimer/first.wav', 'Aimer/second.wav'])}
    first = client.post('/api/training/import-folder', files=payload, data=fields)
    assert first.status_code == 201, first.text
    assert first.json()['imported_count'] == 2
    repeat = client.post('/api/training/import-folder', files=payload, data=fields).json()
    assert repeat['reused_count'] == 2 and repeat['imported_count'] == 0
    folder = client.get('/api/training/folders').json()['items'][0]
    assert folder['singer'] == 'Aimer' and len(folder['source_ids']) == 2
    for item in folder['items']:
        assert '歌手/Aimer/原始素材/' in item['location']
        assert item['name'].split('.')[0] in store.path(item['id']).name
    before = {item['id'] for item in store.inventory()['items']}
    bad = client.post('/api/training/import-folder', files=[('files', ('new.wav', wav(frequency=220))), ('files', ('bad.wav', b'broken'))], data={'singer':'Aimer','kind':'dry_vocal'})
    assert bad.status_code == 400
    assert {item['id'] for item in store.inventory()['items']} == before
    nested = client.post('/api/training/import-folder', files=payload, data={**fields, 'paths':json.dumps(['Aimer/sub/a.wav','Aimer/b.wav'])})
    assert nested.status_code == 400
    for invalid in ('{}', '[1, 2]', '["Aimer/a.wav", "其他歌手/b.wav"]'):
        assert client.post('/api/training/import-folder', files=payload, data={**fields, 'paths':invalid}).status_code == 400


def test_training_and_conversion_materials_are_isolated(tmp_path):
    client = TestClient(create_app(tmp_path))
    training = client.post('/api/sources', files={'file':('training.wav',wav())}, data={'singer':'中文歌手','kind':'dry_vocal','purpose':'training'}).json()
    conversion = client.post('/api/sources', files={'file':('song.wav',wav())}, data={'singer':'原唱','kind':'song','purpose':'conversion'}).json()
    folders = client.get('/api/training/folders').json()['items']
    assert folders[0]['source_ids'] == [training['id']]
    assert '歌手/原唱/转换素材/' in conversion['location']
    assert client.post('/api/datasets/prepare', json={'source_ids':[conversion['id']]}).status_code == 400
    assert client.post('/api/song-separation',json={'source_id':training['id']}).status_code == 400


def test_shorter_than_one_second_cannot_be_manually_accepted(tmp_path):
    app = create_app(tmp_path)
    client, store = TestClient(app), app.state.store
    clips = []
    for seconds in (.999, 1., 1.001):
        item = store.import_stream(BytesIO(wav(seconds)), name=f'{seconds}.wav', metadata={'singer':'歌手','interval':{'duration':seconds}})
        clips.append({'artifact_id':item['id'],'status':'review','duration':seconds,'source_group':'same','duplicate_group':str(seconds),'reasons':[]})
    manifest = store.import_stream(BytesIO(json.dumps({'clips':clips,'sources':[],'summary':{},'singer':'歌手'}).encode()),name='歌手-after.json',role='dataset',metadata={'kind':'dataset_manifest','singer':'歌手'})
    path=f"/api/datasets/{manifest['id']}/review"
    assert client.post(path,json={'decisions':{clips[0]['artifact_id']:'accepted'}}).status_code == 400
    accepted=client.post(path,json={'decisions':{clips[1]['artifact_id']:'accepted',clips[2]['artifact_id']:'accepted'}})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()['name'] == '歌手-ready.json'
    assert '/切片数据集/歌手-ready__' in accepted.json()['location']
    result=client.get(f"/api/datasets/{accepted.json()['id']}").json()
    assert result['clips'][0]['status']=='excluded' and result['summary']['accepted_count']==2


def test_manual_cache_cleanup_overrides_retention_but_respects_active_jobs(tmp_path):
    store=ArtifactStore(tmp_path)
    cached=store.import_stream(BytesIO(b'cache'),name='cache.txt')
    protected=store.import_stream(BytesIO(b'busy'),name='busy.txt')
    permanent=store.import_stream(BytesIO(b'dataset'),name='dataset.json',role='dataset')
    store.retain(cached['id'],True)
    active=store.create_job('test',[protected['id']])
    result=store.cleanup(force=True)
    assert cached['id'] in result['deleted_ids']
    assert store.path(protected['id']).exists() and store.path(permanent['id']).exists()
    assert store.delete_artifacts([permanent['id'],protected['id']])['deleted_ids']==[permanent['id']]
    store.update_job(active['id'],'completed')
    assert store.delete_artifacts([protected['id']])['deleted_ids']==[protected['id']]


def test_cleanup_job_has_inline_progress_and_missing_dataset_status(tmp_path):
    app=create_app(tmp_path)
    client,store=TestClient(app),app.state.store
    clip=store.import_stream(BytesIO(wav()),name='片段.wav')
    manifest=store.import_stream(BytesIO(json.dumps({'clips':[{'artifact_id':clip['id'],'status':'review'}],'sources':[]}).encode()),name='歌手-after.json',role='dataset',metadata={'kind':'dataset_manifest'})
    preview=client.post('/api/cache/preview',json={'force':True}).json()
    assert preview['affected_datasets'][0]['clip_count']==1
    job=client.post('/api/storage/cleanup-jobs',json={'force':True}).json()
    assert run_one(store)
    state=store.job(job['id'])
    assert state['status']=='completed' and state['metadata']['done']==state['metadata']['total']==1
    assert clip['id'] in state['metadata']['cleanup_result']['deleted_ids']
    version=next(i for i in client.get('/api/artifacts').json()['items'] if i['id']==manifest['id'])
    assert version['availability']['missing_count']==1 and version['availability']['available_count']==0


def test_one_click_song_separation_exposes_accompaniment_and_backing(tmp_path,monkeypatch):
    app=create_app(tmp_path)
    client,store=TestClient(app),app.state.store
    source=client.post('/api/sources',files={'file':('测试曲.wav',wav())},data={'kind':'song','purpose':'conversion','singer':'原唱'}).json()
    store.set_runtime('engines',{'rvc':{'ready':True},'separation':{'models':[{'id':'vocals_melband_unwa','ready':True},{'id':'lead_melband_aufr33','ready':True}]}})
    calls=[]
    def separate(store,job_id,source_id,model_id,**kwargs):
        calls.append((source_id,model_id))
        stems=['vocals','instrumental'] if model_id=='vocals_melband_unwa' else ['lead','backing']
        return {stem:store.import_stream(BytesIO(wav()),name=f'测试曲-{stem}.wav',job_id=job_id,metadata={'kind':'separated_audio','stem':stem,'source_id':source_id})['id'] for stem in stems}
    monkeypatch.setattr('voice_workbench_worker.runner.separate',separate)
    job=client.post('/api/song-separation',json={'source_id':source['id']}).json()
    assert run_one(store)
    outcome=store.job(job['id'])
    assert outcome['status']=='completed'
    outputs=outcome['metadata']['outputs']
    assert set(outputs)=={'vocals','instrumental','lead','backing'}
    assert calls[1][0]==outputs['vocals']
    for key in ('instrumental','backing'):
        assert client.get(f"/api/artifacts/{outputs[key]}/file").status_code==200
        assert store.get(outputs[key])['metadata']['purpose']=='conversion'


def test_three_track_remix_includes_backing(tmp_path):
    store=ArtifactStore(tmp_path)
    tracks=[store.import_stream(BytesIO(wav(frequency=f)),name=f'{f}.wav',role='source',metadata={'purpose':'conversion','singer':'原唱'}) for f in (220,440,660)]
    job=store.create_job('mix',[t['id'] for t in tracks],status='running')
    output=mix(store,job['id'],tracks[0]['id'],tracks[1]['id'],backing_id=tracks[2]['id'],backing_db=-6)
    x,sr=sf.read(store.path(output))
    assert sr==44100 and x.ndim==2 and len(x)==sr*3
    assert store.get(output)['metadata']['backing_id']==tracks[2]['id']
    assert store.get(output)['name'].endswith('-mix.wav')

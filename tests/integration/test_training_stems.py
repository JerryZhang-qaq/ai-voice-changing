from io import BytesIO
import hashlib
import json
import sys

from fastapi.testclient import TestClient
import numpy as np
import pytest
import soundfile as sf

from voice_workbench_api.app import create_app
from voice_workbench_engines import separation
from voice_workbench_engines.registry import SEPARATION_MODELS
from voice_workbench_storage import ArtifactStore
from voice_workbench_worker.runner import run_one


def audio(silent=False):
    stream = BytesIO()
    x = np.zeros(64000, dtype=np.float32) if silent else .15 * np.sin(2 * np.pi * 440 * np.arange(64000) / 16000)
    sf.write(stream, x, 16000, format='WAV', subtype='FLOAT')
    return stream.getvalue()


def test_single_training_stem_reuses_cache_without_unused_output(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / 'runtime')
    source = store.import_stream(BytesIO(audio()), name='曲目.wav', role='source',
                                 metadata={'kind': 'song', 'singer': '测试歌手', 'purpose': 'training'})
    spec = SEPARATION_MODELS['vocals_melband_unwa']
    models = tmp_path / 'models'
    models.mkdir()
    for key in ('filename', 'config'):
        (models / spec[key]).write_bytes(b'explicit engine test fixture')
    monkeypatch.setattr(separation, 'settings', lambda: {'separation_models': str(models), 'separation_python': sys.executable})
    monkeypatch.setattr(separation, 'status', lambda: {
        'environment': {'ready': True, 'package_version': '0.47.0'},
        'models': [{'id': 'vocals_melband_unwa', 'downloaded': True}],
    })
    requests = []

    def fixture_engine(command, *, cwd, log, **kwargs):
        request = json.loads((cwd / 'request.json').read_text(encoding='utf-8'))
        requests.append(request)
        for stem in request['keep_stems']:
            (cwd / f'{stem}.wav').write_bytes(audio())
        (cwd / 'response.json').write_text(json.dumps({'outputs': [f'{s}.wav' for s in request['keep_stems']]}))
        log.write_text('explicit test engine exited: 0\n', encoding='utf-8')

    monkeypatch.setattr(separation, 'run_process', fixture_engine)
    # Full output remains available for standalone separation/conversion.
    first = store.create_job('separation', [source['id']], status='running')
    full = separation.separate(store, first['id'], source['id'], 'vocals_melband_unwa')
    assert set(full) == {'vocals', 'instrumental'}
    store.update_job(first['id'], 'completed')
    assert store.delete_artifacts([full['instrumental']])['deleted_ids'] == [full['instrumental']]
    second = store.create_job('dataset_prepare', [source['id']], status='running')
    reused = separation.separate(store, second['id'], source['id'], 'vocals_melband_unwa', keep_stems=['vocals'])
    assert reused == {'vocals': full['vocals']}
    assert len(requests) == 1, 'A missing unused accompaniment must not trigger inference again'
    # A profile change gets its own identity, but users can explicitly reuse
    # an existing quality result (including the old 0.0.3 key format).
    quality_job = store.create_job('dataset_prepare', [source['id']], status='running')
    quality = separation.separate(store, quality_job['id'], source['id'], 'vocals_melband_unwa', profile='quality', keep_stems=['vocals'])
    assert len(requests) == 2 and requests[-1]['precision'] == 'fp32'
    fast_job = store.create_job('dataset_prepare', [source['id']], status='running')
    assert separation.separate(store, fast_job['id'], source['id'], 'vocals_melband_unwa', profile='fast', keep_stems=['vocals'], reuse_quality_cache=True) == quality
    assert len(requests) == 2
    legacy_job = store.create_job('separation', [source['id']], status='running')
    legacy_key = {'source': source['sha256'], 'singer': '测试歌手', 'purpose': 'training',
                  'weight': separation.sha256_file(models / spec['filename']),
                  'config': separation.sha256_file(models / spec['config']),
                  'engine_version': '0.47.0', 'segment_size': 256, 'overlap': 8, 'task': spec['task'], 'stem': 'vocals'}
    legacy = store.import_stream(BytesIO(audio()), name='曲目-vocals-legacy.wav', job_id=legacy_job['id'],
                    metadata={'cache_key': hashlib.sha256(json.dumps(legacy_key, sort_keys=True).encode()).hexdigest(), 'kind': 'separated_audio'})
    store.update_job(quality_job['id'], 'completed')
    store.update_job(fast_job['id'], 'completed')
    store.update_job(legacy_job['id'], 'completed')
    assert store.delete_artifacts(list(quality.values()))['deleted_ids'] == list(quality.values())
    reused_legacy = store.create_job('dataset_prepare', [source['id']], status='running')
    assert separation.separate(store, reused_legacy['id'], source['id'], 'vocals_melband_unwa', profile='fast', keep_stems=['vocals'], reuse_quality_cache=True) == {'vocals': legacy['id']}
    assert len(requests) == 2
    forced = store.create_job('dataset_prepare', [source['id']], status='running')
    separation.separate(store, forced['id'], source['id'], 'vocals_melband_unwa', profile='fast', keep_stems=['vocals'])
    assert len(requests) == 3 and requests[-1]['overlap'] == 2
    other = store.import_stream(BytesIO(audio(silent=True)), name='另一首.wav', role='source')
    third = store.create_job('dataset_prepare', [other['id']], status='running')
    output = separation.separate(store, third['id'], other['id'], 'vocals_melband_unwa', keep_stems=['vocals'])
    assert set(output) == {'vocals'} and requests[-1]['keep_stems'] == ['vocals']
    assert {a['metadata'].get('stem') for a in store.inventory()['items'] if a['job_id'] == third['id']
            and a['metadata'].get('kind') == 'separated_audio'} == {'vocals'}
    for invalid in ([], ['backing']):
        with pytest.raises(ValueError, match='输出声部'):
            separation.separate(store, third['id'], other['id'], 'vocals_melband_unwa', keep_stems=invalid)


def test_default_training_skips_harmony_but_conversion_keeps_all_stems(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'runtime')
    client, store = TestClient(app), app.state.store
    store.set_runtime('engines', {'separation': {'models': [{'id': mid, 'ready': True} for mid in SEPARATION_MODELS]}})
    calls = []

    def fixture_separate(store, job_id, source_id, model_id, *, keep_stems=None, **kwargs):
        spec = SEPARATION_MODELS[model_id]
        wanted = list(spec['stems'].values()) if keep_stems is None else keep_stems
        calls.append((model_id, set(wanted)))
        return {stem: store.import_stream(BytesIO(audio(silent=stem in {'backing', 'instrumental', 'noise', 'reverb'})),
                    name=f'曲目-{stem}.wav', job_id=job_id,
                    metadata={'kind': 'separated_audio', 'stem': stem, 'task': spec['task'], 'source_id': source_id})['id']
                for stem in wanted}

    monkeypatch.setattr('voice_workbench_worker.runner.separate', fixture_separate)
    source = client.post('/api/sources', files={'file': ('曲目.wav', audio())},
                         data={'singer': '测试歌手', 'kind': 'song', 'purpose': 'training'})
    assert source.status_code == 201
    response = client.post('/api/datasets/prepare', json={
        'folder_singer': '测试歌手', 'solo_confirmed': True, 'dereverb': True, 'denoise': True,
    })
    assert response.status_code == 202, response.text
    assert run_one(store)
    state = store.job(response.json()['id'])
    assert state['status'] == 'completed', state
    stems = {a['metadata']['stem'] for a in store.inventory()['items'] if a['job_id'] == state['id']
             and a['metadata'].get('kind') == 'separated_audio'}
    assert stems == {'vocals', 'dry', 'clean'}
    manifest = client.get(f"/api/datasets/{state['metadata']['result_id']}").json()
    assert manifest['summary']['clip_count'] > 0
    assert manifest['harmony_review'] == {'scope': 'clip', 'enabled': False}
    assert calls == [('vocals_melband_unwa', {'vocals'}),
                     ('dereverb_melband_anvuew', {'dry'}), ('denoise_melband_aufr33', {'clean'})]
    converted = client.post('/api/sources', files={'file': ('待翻唱.wav', audio())},
                            data={'singer': '原唱', 'kind': 'song', 'purpose': 'conversion'}).json()
    job = client.post('/api/song-separation', json={'source_id': converted['id']}).json()
    assert run_one(store)
    result = store.job(job['id'])
    assert result['status'] == 'completed', result
    assert set(result['metadata']['outputs']) == {'vocals', 'instrumental', 'lead', 'backing'}


def test_batch_groups_songs_by_model_and_shares_one_session(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'runtime')
    client, store = TestClient(app), app.state.store
    store.set_runtime('engines', {'separation': {'models': [{'id': mid, 'ready': True} for mid in SEPARATION_MODELS]}})
    calls = []

    def engine(store, job_id, source_id, model_id, *, session, keep_stems, progress, **kwargs):
        calls.append((model_id, session))
        progress()
        return {stem: store.import_stream(BytesIO(audio()), name=f'{source_id}-{stem}.wav', job_id=job_id,
                  metadata={'kind': 'separated_audio', 'stem': stem, 'source_id': source_id})['id'] for stem in keep_stems}

    monkeypatch.setattr('voice_workbench_worker.runner.separate', engine)
    for name in ('第一首.wav', '第二首.wav'):
        assert client.post('/api/sources', files={'file': (name, audio())},
                           data={'singer': '测试歌手', 'kind': 'song', 'purpose': 'training'}).status_code == 201
    response = client.post('/api/datasets/prepare', json={
        'folder_singer': '测试歌手', 'solo_confirmed': True, 'dereverb': True, 'denoise': True,
    })
    assert response.status_code == 202, response.text
    assert run_one(store)
    result = store.job(response.json()['id'])
    assert result['status'] == 'completed', result
    assert [model for model, _ in calls] == ['vocals_melband_unwa'] * 2 + ['dereverb_melband_anvuew'] * 2 + ['denoise_melband_aufr33'] * 2
    assert len({id(session) for _, session in calls}) == 1
    assert result['metadata']['batch_progress']['done'] == 8
    assert result['metadata']['batch_progress']['total'] == 8

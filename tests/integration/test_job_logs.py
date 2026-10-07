from io import BytesIO

from fastapi.testclient import TestClient
import pytest

from voice_workbench_api.app import create_app


def saved_log(store, job_id, text):
    return store.import_stream(BytesIO(text.encode('utf-8')), name='engine.log',
                               job_id=job_id, metadata={'kind': 'engine_log'})


@pytest.mark.parametrize('padding', [0, 70000])
def test_separation_logs_are_chronological_and_keep_the_latest_tail(tmp_path, padding):
    app = create_app(tmp_path)
    store = app.state.store
    job = store.create_job('dataset_prepare', status='running')
    first = saved_log(store, job['id'], '09:03 提取人声开始\n' + 'x' * padding + '\n09:05 人声提取完成\n')
    second = saved_log(store, job['id'], '09:06 和声分离开始\n' + 'y' * padding + '\n09:08 和声分离完成\n')
    with store.connect(write=True) as db:
        db.execute('UPDATE artifacts SET created_at=100 WHERE id=?', (first['id'],))
        db.execute('UPDATE artifacts SET created_at=200 WHERE id=?', (second['id'],))
    response = TestClient(app).get(f"/api/jobs/{job['id']}/logs")
    assert response.status_code == 200
    text = response.json()['text']
    assert text.endswith('09:08 和声分离完成\n')
    assert len(text) <= 65536
    if not padding:
        assert text.index('09:05 人声提取完成') < text.index('09:06 和声分离开始')


def test_live_workspace_log_follows_saved_steps(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    job = store.create_job('cover', status='running')
    saved_log(store, job['id'], '分离步骤已完成\n')
    workspace = store.allocate_workspace(job['id'], name='当前步骤')
    store.update_job(job['id'], 'running', metadata={'workspace_id': workspace['id']})
    (store.path(workspace['id']) / 'engine.log').write_text('当前步骤正在运行\n', encoding='utf-8')
    response = TestClient(app).get(f"/api/jobs/{job['id']}/logs")
    assert response.status_code == 200
    assert response.json()['text'] == '分离步骤已完成\n\n当前步骤正在运行\n'


def test_missing_log_file_does_not_hide_remaining_records(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    job = store.create_job('dataset_prepare', status='running')
    missing = saved_log(store, job['id'], '已丢失的旧记录\n')
    saved_log(store, job['id'], '仍可读取的新记录\n')
    store.path(missing['id']).unlink()
    response = TestClient(app).get(f"/api/jobs/{job['id']}/logs")
    assert response.status_code == 200
    assert response.json()['text'] == '仍可读取的新记录\n'


def test_live_separation_log_is_readable_before_engine_exit(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    job = store.create_job('dataset_prepare', status='running')
    directory = store.root / f"processing-{job['id']}-session-test"
    directory.mkdir()
    (directory / 'engine.log').write_text('分块进度 5/100\n', encoding='utf-8')
    store.update_job(job['id'], 'running', metadata={'live_engine_log': f'{directory.name}/engine.log'})
    client = TestClient(app)
    assert client.get(f"/api/jobs/{job['id']}/logs").json()['text'] == '分块进度 5/100\n'
    store.update_job(job['id'], 'running', metadata={'live_engine_log': '../private/engine.log'})
    assert client.get(f"/api/jobs/{job['id']}/logs").json()['text'] == ''

"""Run the real updater against isolated old-install fixtures on both OSes."""
from io import BytesIO
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from voice_workbench_storage import ArtifactStore, NotFound
from voice_workbench_storage.locking import WorkerLock

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('workbench_upgrade',ROOT/'scripts/upgrade.py')
updater=importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def fixture(tmp_path):
    source=tmp_path/'new package'
    target=tmp_path/'原目录 空格 & test'
    source.mkdir();target.mkdir()
    (source/'scripts').mkdir()
    (source/'pyproject.toml').write_text(f'[project]\nname="voice-workbench"\nversion="{updater.VERSION}"\n')
    (source/'scripts/new.py').write_text('print("new")\n')
    manifest={}
    for name in {*updater.REQUIRED_FILES, 'scripts/new.py'}:
        path = source / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'isolated upgrade fixture')
        data=(source/name).read_bytes()
        manifest[name]={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    (source/'RELEASE-MANIFEST.json').write_text(json.dumps(manifest))
    (target/'pyproject.toml').write_text('[project]\nname="voice-workbench"\nversion="0.0.1"\n')
    (target/'.venv').mkdir();(target/'.venv/sentinel').write_bytes(b'environment')
    store=ArtifactStore(target/'runtime')
    old=store.import_stream(BytesIO(b'source'),name='old.wav',role='source')
    dataset=store.import_stream(BytesIO(b'{}'),name='old-ready.json',role='dataset')
    model=store.import_stream(BytesIO(b'weights'),name='trained.pth',role='model',metadata={'kind':'rvc_model'})
    index=store.import_stream(BytesIO(b'index'),name='trained.index',role='model',metadata={'kind':'rvc_index','model_id':model['id']})
    final=store.import_stream(BytesIO(b'cover'),name='cover.wav',role='export',metadata={'kind':'mixed_audio'})
    return source,target,store,[old,dataset],[model,index,final]


def test_overwrite_and_repeat_preserve_all_data(tmp_path):
    source,target,store,old,retained=fixture(tmp_path)
    updater.upgrade(source,target)
    assert updater.VERSION in (target/'pyproject.toml').read_text()
    assert (target/'scripts/new.py').read_text()=='print("new")\n'
    assert (target/'.venv/sentinel').read_bytes()==b'environment'
    assert all(store.path(item['id']).is_file() for item in old)
    for item in retained:
        assert store.path(item['id']).read_bytes() in {b'weights',b'index',b'cover'}
    assert (target/f'runtime/diagnostics/catalog-before-{updater.VERSION}.sqlite3').is_file()
    fresh=store.import_stream(BytesIO(b'new source'),name='new.wav',role='source')
    updater.upgrade(source,target)
    assert store.path(fresh['id']).read_bytes()==b'new source'


def test_active_worker_rejects_before_overwriting_or_deleting(tmp_path):
    source,target,store,old,retained=fixture(tmp_path)
    with WorkerLock(store.root/'worker.lock'):
        with pytest.raises(RuntimeError,match='关闭旧工作台'):
            updater.upgrade(source,target)
    assert '0.0.1' in (target/'pyproject.toml').read_text()
    assert not (target/'scripts/new.py').exists()
    assert all(store.path(item['id']).is_file() for item in old+retained)


def test_corrupt_package_rejected_before_any_target_mutation(tmp_path):
    source,target,store,old,retained=fixture(tmp_path)
    (source/'scripts/new.py').write_text('corrupt')
    with pytest.raises(ValueError,match='完整性'):
        updater.upgrade(source,target)
    assert '0.0.1' in (target/'pyproject.toml').read_text()
    assert all(store.path(item['id']).is_file() for item in old+retained)


def test_fresh_current_install_keeps_data_without_old_upgrade_receipt(tmp_path):
    source,target,store,old,retained=fixture(tmp_path)
    (target/'pyproject.toml').write_bytes((source/'pyproject.toml').read_bytes())
    store.set_runtime('app_version', {'version':updater.VERSION})
    updater.upgrade(source,target)
    assert all(store.path(item['id']).is_file() for item in old+retained)
    receipt=json.loads((target/f'runtime/diagnostics/upgrade-{updater.VERSION}.json').read_text())
    assert receipt['fresh_install'] and receipt['deleted_ids']==[]

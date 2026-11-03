"""Cloud access tests use synthetic video and explicit mocks, never paid calls."""
from dataclasses import replace
import json

from fastapi.testclient import TestClient
import pytest

from backend.cloud import COOKIE, TrustedProxyHostMiddleware
from backend.main import create_app
from backend.errors import AppError
from tests.conftest import MockVisionProvider, wait_job


@pytest.fixture
def cloud_app(settings):
    return create_app(replace(settings, cloud_mode=True, cloud_max_videos=4,
        cloud_videos_per_visitor=2, max_video_seconds=120, max_upload_bytes=50*1024**2,
        allowed_hosts=('localhost', 'studio-test.ms.show')), MockVisionProvider())


def test_visitor_isolation_covers_api_jobs_media_and_deletion(cloud_app, sample_video):
    with TestClient(cloud_app, base_url='https://studio-test.ms.show') as first:
        h = first.get('/api/health')
        assert h.json()['cloud_mode']
        assert h.headers['cache-control']=='no-store'
        cookie = h.headers['set-cookie']
        assert all(x in cookie for x in ['HttpOnly', 'Secure', 'SameSite=none', 'Partitioned'])
        response = first.post('/api/videos/upload', content=sample_video.read_bytes(), headers={'Origin':'https://studio-test.ms.show'})
        assert response.status_code == 201, response.text
        vid = response.json()['video_id']
        job = wait_job(first, first.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status']=='completed'
        memory = first.get(f'/api/videos/{vid}/events').json()
        fid = memory['frames'][0]['id']
        assert 'cloud_visitor' not in first.get(f'/api/videos/{vid}').json()['metadata']
        with TestClient(cloud_app, base_url='https://studio-test.ms.show') as second:
            assert second.get('/api/videos').json()['videos']==[]
            assert second.get('/api/demos').json()['demos']==[]
            for path in [f'/api/videos/{vid}', f'/api/videos/{vid}/events', f'/api/videos/{vid}/alerts',
                         f'/api/videos/{vid}/analysis-plan', f'/api/jobs/{job["id"]}',
                         f'/media/videos/{vid}', f'/media/frames/{vid}/{fid}']:
                assert second.get(path).status_code==404, path
            assert second.post(f'/api/videos/{vid}/query', json={'question':'钥匙在哪里？'}).status_code==404
            assert second.post(f'/api/videos/{vid}/analyze').status_code==404
            assert second.delete(f'/api/videos/{vid}').status_code==404
            assert second.post(f'/api/videos/{vid}/delete').status_code==404
        assert first.get(f'/media/videos/{vid}', headers={'Range':'bytes=0-99'}).status_code==206
        assert first.post(f'/api/videos/{vid}/query', json={'question':'钥匙在哪里？'}).json()['evidence']
        assert first.post(f'/api/videos/{vid}/delete').status_code==200


def test_verified_public_example_is_visible_but_cannot_be_mutated(cloud_app, sample_video):
    with TestClient(cloud_app, base_url='https://studio-test.ms.show') as first:
        vid = first.post('/api/videos/upload', content=sample_video.read_bytes()).json()['video_id']
        wait_job(first, first.post(f'/api/videos/{vid}/analyze').json()['id'])
        # Synthetic test fixture simulating the trusted importer flag.
        db=cloud_app.state.db
        video=db.video(vid)
        video['metadata']['imported_real_recording']=True
        with db.connect() as c:
            c.execute('UPDATE videos SET metadata=? WHERE id=?',(json.dumps(video['metadata']),vid))
        with TestClient(cloud_app, base_url='https://studio-test.ms.show') as second:
            assert second.get('/api/videos').json()['videos'][0]['read_only_demo']
            demos=second.get('/api/demos').json()['demos']
            assert len(demos)==1 and demos[0]['id']==vid and demos[0]['record_count']>0
            assert demos[0]['poster_url'].startswith('/media/frames/'+vid+'/')
            assert 'cloud_visitor' not in json.dumps(demos) and 'original_path' not in json.dumps(demos)
            assert second.get(f'/api/videos/{vid}/events').status_code==200
            assert second.post(f'/api/videos/{vid}/query',json={'question':'钥匙在哪里？'}).json()['evidence']
            for action in ['analyze','resume','pause','regions','review/e1/verify','alerts/a1/acknowledge']:
                assert second.post(f'/api/videos/{vid}/{action}',json={}).status_code==403, action
            assert second.delete(f'/api/videos/{vid}').status_code==403
            assert second.post(f'/api/videos/{vid}/delete').status_code==403


def test_upload_reservations_release_on_failure_and_enforce_capacity(cloud_app, sample_video):
    with TestClient(cloud_app, base_url='https://studio-test.ms.show') as client:
        assert client.post('/api/videos/upload',content=b'not a video').status_code==400
        assert client.get('/api/videos').json()['videos']==[]
        for _ in range(2):
            assert client.post('/api/videos/upload',content=sample_video.read_bytes()).status_code==201
        assert client.post('/api/videos/upload',content=sample_video.read_bytes()).json()['error']['code']=='VISITOR_CAPACITY'
        # A fresh visitor cannot bypass the shared daily provider ledger.
        assert cloud_app.state.db.usage_summary()['requests']==0


def test_external_origin_and_unconfigured_hosts_are_rejected(cloud_app):
    with TestClient(cloud_app, base_url='https://studio-test.ms.show') as client:
        assert client.post('/api/videos/upload',content=b'test',headers={'Origin':'https://attacker.invalid'}).status_code==403
        assert client.get('/api/health',headers={'Host':'attacker.invalid'}).status_code==400
        client.cookies.set(COOKIE, 'guessed')
        assert 'guessed;' not in client.get('/api/health').headers['set-cookie']


def test_forwarded_host_requires_trusted_peer_and_exact_domain(settings):
    app = create_app(replace(settings, cloud_mode=True,
        allowed_hosts=('studio-test.ms.show',), trusted_proxy_cidrs=('10.1.2.3/32',)), MockVisionProvider())
    with TestClient(app, base_url='https://internal.fcapp.run', client=('10.1.2.3', 1234)) as client:
        assert client.get('/api/health', headers={'X-Forwarded-Host':'studio-test.ms.show'}).status_code==200
        for host in ['attacker.invalid', 'studio-test.ms.show,attacker.invalid', 'studio-test.ms.show:443']:
            assert client.get('/api/health', headers={'X-Forwarded-Host':host}).status_code==400
        assert client.get('/api/health').status_code==400
    with TestClient(app, base_url='https://internal.fcapp.run', client=('203.0.113.2', 1234)) as client:
        assert client.get('/api/health', headers={'X-Forwarded-Host':'studio-test.ms.show'}).status_code==400


def test_proxy_configuration_rejects_subnets():
    for subnet in ['0.0.0.0/0','10.0.0.0/8','169.254.0.0/16','::/0']:
        with pytest.raises(ValueError, match='exact IP'):
            TrustedProxyHostMiddleware(None, (subnet,), ('studio-test.ms.show',))


def test_cloud_journal_and_interrupted_accounting_preserve_quota(cloud_app):
    db=cloud_app.state.db
    with db.connect() as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    db.reserve_request('synthetic-video','synthetic-job',1,1)
    db.interrupt_running(mark_reserved_unknown=True)
    usage=db.usage_summary()
    assert usage['requests']==1 and usage['pending']==0 and usage['unknown']==1
    with pytest.raises(AppError) as error:
        db.reserve_request('synthetic-video','synthetic-job',1,1)
    assert error.value.code=='REQUEST_LIMIT'


def test_cloud_backup_closes_before_journal_switch(settings, monkeypatch):
    from backend.db import Database
    from scripts import cloud_entrypoint
    config=replace(settings, cloud_mode=True, max_concurrency=1,
        max_video_seconds=120, max_upload_bytes=50*1024**2, max_daily_calls=60)
    Database(config.data_dir)  # Existing WAL database before cloud migration.
    monkeypatch.setattr(cloud_entrypoint.Settings, 'from_env', lambda:config)
    def import_without_network(bundle, selected):
        db=Database(selected.data_dir, journal_mode='DELETE')
        with db.connect() as c:
            assert c.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        return {'status':'test-only fixture'}
    monkeypatch.setattr(cloud_entrypoint, 'import_all_recordings', import_without_network)
    monkeypatch.setattr(cloud_entrypoint.os, 'execv', lambda *args:None)
    cloud_entrypoint.main()
    assert (config.data_dir/'memory.before-delete-mode.sqlite3').stat().st_size>0


def public_fixture(client, app, sample_video):
    """Synthetic fixture flagged as an imported demo for access-control tests."""
    vid=client.post('/api/videos/upload',content=sample_video.read_bytes()).json()['video_id']
    wait_job(client,client.post(f'/api/videos/{vid}/analyze').json()['id'])
    db=app.state.db
    video=db.video(vid)
    video['metadata'].update(imported_real_recording=True,playback_derivative={'test_only':True})
    with db.connect() as c:
        c.execute('UPDATE videos SET metadata=?,sha256=? WHERE id=?',
                  (json.dumps(video['metadata']),'original-historical-input-hash',vid))
    return vid


def test_demo_reanalysis_is_fresh_private_and_preserves_source(cloud_app,sample_video):
    from backend.media import file_sha256
    from pathlib import Path
    with TestClient(cloud_app,base_url='https://studio-test.ms.show') as owner:
        vid=public_fixture(owner,cloud_app,sample_video)
        before=cloud_app.state.db.raw_memory(vid)
        original=cloud_app.state.db.video(vid)
        with TestClient(cloud_app,base_url='https://studio-test.ms.show') as judge:
            response=judge.post(f'/api/videos/{vid}/reanalyze')
            assert response.status_code==202,response.text
            job=wait_job(judge,response.json()['id'])
            copy_id=job['video_id']
            assert copy_id!=vid and job['status']=='completed' and job['id']!=before[1]['id']
            copied=cloud_app.state.db.video(copy_id)
            assert not copied['metadata'].get('imported_real_recording')
            assert copied['metadata']['reanalysis_of']==vid
            assert copied['metadata']['reanalysis_input']=='compressed_playback'
            assert copied['sha256']==file_sha256(Path(original['original_path']))
            assert not judge.get(f'/api/videos/{copy_id}').json()['read_only_demo']
            assert judge.get(f'/api/videos/{copy_id}/events').json()['is_mock']  # Explicit test provider.
            assert all(d['id']!=copy_id for d in judge.get('/api/demos').json()['demos'])
            assert owner.get(f'/api/videos/{copy_id}').status_code==404
            assert owner.post(f'/api/videos/{copy_id}/reanalyze').status_code==404
            assert judge.post(f'/api/videos/{copy_id}/reanalyze').status_code==400
            assert judge.post(f'/api/videos/{copy_id}/delete').status_code==200
        assert cloud_app.state.db.raw_memory(vid)==before
        assert Path(original['original_path']).is_file()
        assert owner.get(f'/media/videos/{vid}',headers={'Range':'bytes=0-9'}).status_code==206


def test_demo_reanalysis_capacity_and_failed_start_release_slots(cloud_app,sample_video,monkeypatch):
    with TestClient(cloud_app,base_url='https://studio-test.ms.show') as client:
        vid=public_fixture(client,cloud_app,sample_video)
        manager=cloud_app.state.manager
        original_start=manager.start
        def busy(*args,**kwargs):
            raise AppError('BUSY','Test only busy provider',429)
        monkeypatch.setattr(manager,'start',busy)
        assert client.post(f'/api/videos/{vid}/reanalyze').json()['error']['code']=='BUSY'
        assert len(client.get('/api/videos').json()['videos'])==1
        monkeypatch.setattr(manager,'start',original_start)
        for _ in range(2):
            response=client.post(f'/api/videos/{vid}/reanalyze')
            assert response.status_code==202,response.text
            assert wait_job(client,response.json()['id'])['status']=='completed'
        assert client.post(f'/api/videos/{vid}/reanalyze').json()['error']['code']=='VISITOR_CAPACITY'


def test_demo_reanalysis_respects_daily_quota(cloud_app,sample_video,monkeypatch):
    with TestClient(cloud_app,base_url='https://studio-test.ms.show') as client:
        vid=public_fixture(client,cloud_app,sample_video)
        db=cloud_app.state.db
        quota=cloud_app.state.settings.max_daily_calls
        for _ in range(quota):
            db.reserve_request(vid,'test-only-accounting',quota,quota)
        monkeypatch.setattr(cloud_app.state.manager.provider,'is_mock',False)
        response=client.post(f'/api/videos/{vid}/reanalyze')
        assert response.status_code==429 and response.json()['error']['code']=='REQUEST_LIMIT'
        assert len(client.get('/api/videos').json()['videos'])==1
        assert db.usage_summary()['requests']==quota


def test_long_demo_reanalysis_does_not_apply_upload_duration_limit(cloud_app,sample_video,monkeypatch):
    with TestClient(cloud_app,base_url='https://studio-test.ms.show') as client:
        vid=public_fixture(client,cloud_app,sample_video)
        with cloud_app.state.db.connect() as c:
            c.execute('UPDATE videos SET duration_ms=393633 WHERE id=?',(vid,))
        # Test admission only, without pretending the synthetic 12s clip is long footage.
        def admit(copy_id):
            video=cloud_app.state.db.video(copy_id)
            assert video['duration_ms']==393633 and video['active_run'] is None
            return {'id':'test-only-admission','video_id':copy_id,'status':'queued','is_mock':True}
        monkeypatch.setattr(cloud_app.state.manager,'start',admit)
        assert client.post(f'/api/videos/{vid}/reanalyze').status_code==202

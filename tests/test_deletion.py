"""Deletion uses only isolated synthetic uploads and mock results, never user assets."""
from pathlib import Path
from unittest.mock import patch

import pytest


def test_delete_removes_owned_results_reviews_and_files_but_preserves_usage(client, analyzed, sample_video):
    vid, memory = analyzed
    db = client.app.state.db
    settings = client.app.state.settings
    job = memory['job']
    original, _ = db.raw_memory(vid)
    state = client.get(f'/api/videos/{vid}/review').json()
    event = original['events'][0]
    sampled = client.post(f'/api/videos/{vid}/review/{event["id"]}/sample',json={
        'run_id':job['id'],'expected_revision':state['revision'],'center_ms':1000})
    assert sampled.status_code == 200
    reviews = db.review_records(vid)
    sample_id = next(r['payload']['sample_id'] for r in reviews if r['kind'] == 'evidence_started')
    batch = job['id']+'-b2'
    batch_dir = settings.data_dir/'runs'/batch
    batch_dir.mkdir();(batch_dir/'SYNTHETIC_RESPONSE_ONLY.json').write_text('{}')
    request_id = db.reserve_request(vid,batch,24,12)
    db.finish_request(request_id,'success',{'usage':{'prompt_tokens':100,'completion_tokens':20}})
    charge = db.reserve(vid,batch,.1,2,5)
    db.settle(charge,.05,{'prompt_tokens':100,'completion_tokens':20})
    before_usage = db.usage_summary()
    other = client.post('/api/videos/upload',content=sample_video.read_bytes()).json()['video_id']
    other_source = Path(db.video(other)['original_path'])
    other_bytes = other_source.read_bytes()
    result = client.delete(f'/api/videos/{vid}')
    assert result.status_code == 200 and result.json() == {'deleted':True,'video_id':vid,'cleanup_pending':False,'shared_files_retained':False}
    assert not (settings.data_dir/'videos'/vid).exists()
    assert not (settings.data_dir/'runs'/job['id']).exists() and not batch_dir.exists()
    assert not (settings.data_dir/'reviews'/sample_id).exists()
    assert other_source.read_bytes() == other_bytes
    assert db.usage_summary() == before_usage
    with db.connect() as c:
        for table in ['videos','jobs','memories','events','review_log']:
            key = 'id' if table == 'videos' else 'video_id'
            assert c.execute(f'SELECT COUNT(*) FROM {table} WHERE {key}=?',(vid,)).fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM charges WHERE video_id=?',(vid,)).fetchone()[0] == 1
    for path in [f'/api/videos/{vid}',f'/api/videos/{vid}/events',f'/api/videos/{vid}/review',
                 f'/api/jobs/{job["id"]}',f'/media/videos/{vid}',f'/media/frames/{vid}/{memory["frames"][0]["id"]}']:
        assert client.get(path).status_code == 404
    assert client.delete(f'/api/videos/{vid}').status_code == 404


def test_delete_unanalyzed_upload(client,sample_video):
    vid = client.post('/api/videos/upload',content=sample_video.read_bytes()).json()['video_id']
    directory = client.app.state.settings.data_dir/'videos'/vid
    assert client.delete(f'/api/videos/{vid}').status_code == 200
    assert not directory.exists() and client.get('/api/videos').json()['videos'] == []


@pytest.mark.parametrize('busy',['active','queued','review','reserved_request'])
def test_delete_busy_video_is_rejected_without_changes(client,analyzed,busy):
    vid,_ = analyzed
    db = client.app.state.db
    original = db.raw_memory(vid)[0]
    if busy == 'active': client.app.state.manager.active.add(vid)
    if busy == 'review': client.app.state.reviewer.busy.add(vid)
    if busy == 'queued': db.save_job({'id':'synthetic_queued','video_id':vid,'status':'queued'})
    if busy == 'reserved_request': db.reserve_request(vid,'synthetic_reserved',24,12)
    try:
        assert client.delete(f'/api/videos/{vid}').status_code == 409
        assert db.raw_memory(vid)[0] == original
        assert Path(db.video(vid)['original_path']).is_file()
    finally:
        client.app.state.manager.active.discard(vid);client.app.state.reviewer.busy.discard(vid)


def test_shared_original_is_retained_until_last_library_reference_is_deleted(client,analyzed):
    vid,_ = analyzed
    db = client.app.state.db
    source = db.video(vid)
    shared = {**source,'id':'v_SYNTHETIC_SHARED','name':'SYNTHETIC_SHARED.mp4'}
    db.add_video(shared)
    first = client.delete(f'/api/videos/{vid}')
    assert first.status_code == 200 and first.json()['shared_files_retained']
    assert Path(source['original_path']).is_file()
    assert client.get('/media/videos/v_SYNTHETIC_SHARED',headers={'Range':'bytes=0-19'}).status_code == 206
    assert client.delete('/api/videos/v_SYNTHETIC_SHARED').status_code == 200
    assert not Path(source['original_path']).exists()


def test_external_source_and_other_project_files_are_never_removed(client,sample_video):
    db = client.app.state.db
    vid = 'v_SYNTHETIC_EXTERNAL'
    source_bytes = sample_video.read_bytes()
    db.add_video({'id':vid,'name':'SYNTHETIC_EXTERNAL.mp4','sha256':'0'*64,'duration_ms':12000,
                  'original_path':str(sample_video),'playback_path':str(sample_video),'metadata':{}})
    assert client.delete(f'/api/videos/{vid}').status_code == 200
    assert sample_video.read_bytes() == source_bytes


def test_unsafe_managed_path_is_rejected_before_any_files_move(client,analyzed,tmp_path):
    vid,_ = analyzed
    db = client.app.state.db
    original = db.raw_memory(vid)[0]
    outside = tmp_path/'SYNTHETIC_MUST_KEEP.txt';outside.write_text('keep')
    db.save_job({'id':'../../SYNTHETIC_MUST_KEEP','video_id':vid,'status':'completed'})
    result = client.delete(f'/api/videos/{vid}')
    assert result.status_code == 409 and result.json()['error']['code'] == 'DELETE_PATH'
    assert outside.read_text() == 'keep' and db.raw_memory(vid)[0] == original


def test_file_move_failure_rolls_back_database_and_prior_moves(client,analyzed):
    vid,memory = analyzed
    db = client.app.state.db
    original = db.raw_memory(vid)[0]
    rename = Path.rename

    def fail_video_move(path,target):
        if path.name == vid: raise PermissionError('Synthetic injected file in use')
        return rename(path,target)

    with patch.object(Path,'rename',fail_video_move):
        response = client.delete(f'/api/videos/{vid}')
    assert response.status_code == 409 and response.json()['error']['code'] == 'DELETE_FAILED'
    assert db.raw_memory(vid)[0] == original
    assert Path(db.video(vid)['original_path']).is_file()
    assert (client.app.state.settings.data_dir/'runs'/memory['job']['id']/'checkpoint.json').is_file()


def test_cleanup_failure_is_reported_without_claiming_disk_cleanup_completed(client,analyzed):
    vid,_ = analyzed
    with patch('backend.deletion.shutil.rmtree',side_effect=PermissionError('Synthetic cleanup failure')):
        result = client.delete(f'/api/videos/{vid}')
    assert result.status_code == 200 and result.json()['cleanup_pending'] is True
    assert client.get(f'/api/videos/{vid}').status_code == 404


def test_cross_origin_delete_is_blocked(client,analyzed):
    vid,_ = analyzed
    response = client.delete(f'/api/videos/{vid}',headers={'Origin':'https://untrusted.example'})
    assert response.status_code == 403
    assert client.get(f'/api/videos/{vid}').status_code == 200


def test_home_and_workspace_can_both_be_loaded_directly(client):
    for path in ['/','/workspace']:
        response = client.get(path)
        assert response.status_code == 200 and 'id="root"' in response.text

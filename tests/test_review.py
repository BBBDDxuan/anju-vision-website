"""Synthetic fixtures only: review persistence, evidence gating and real media PTS; no paid calls."""
import copy
import json
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.events import validate_memory
from backend.main import create_app
from backend.media import extract_review_frames, inspect_video
from backend.names import localized_document
from backend.review import match_region
from tests.conftest import MockVisionProvider, wait_job


def upload_and_analyze(client, sample_video):
    vid = client.post('/api/videos', files={'file': ('SYNTHETIC_REVIEW_ONLY.mp4', sample_video.read_bytes(), 'video/mp4')}).json()['video_id']
    job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
    # Exercise real-result gating using explicitly synthetic data, not genuine AI claims.
    job['is_mock'] = False
    client.app.state.db.save_job(job)
    client.app.state.db.refresh_active_states()
    return vid, job


def test_critical_claims_quarantined_until_review(settings, sample_video):
    with TestClient(create_app(settings, MockVisionProvider()), base_url='http://localhost') as client:
        vid, job = upload_and_analyze(client, sample_video)
        original, _ = client.app.state.db.raw_memory(vid)
        result = client.get(f'/api/videos/{vid}/events').json()
        assert [e['action'] for e in original['events']] == ['place_on', 'put_in', 'move', 'explicit_exit']
        assert [e['action'] for e in result['events']] == ['place_on', 'put_in', 'move', 'explicit_exit']
        assert result['events'][1]['epistemic_type'] == result['events'][3]['epistemic_type'] == 'inferred'
        answer = client.post(f'/api/videos/{vid}/query', json={'question': '钥匙现在在哪里？'}).json()
        assert answer['inferences'] and all(c['rule_id'] == 'unverified_visual_candidate_v1' for c in answer['inferences'])
        with client.app.state.db.connect() as c:
            cached = json.loads(c.execute('SELECT payload FROM states WHERE run_id=? AND entity_id=?', (job['id'], original['events'][1]['subject_id'])).fetchone()[0])
            assert cached['container_event'] is None
        state = client.get(f'/api/videos/{vid}/review').json()
        event = original['events'][1]
        response = client.post(f'/api/videos/{vid}/review/{event["id"]}/human', json={
            'run_id': job['id'], 'expected_revision': state['revision'], 'decision': 'confirm', 'note': 'Synthetic test confirmation only.'})
        assert response.status_code == 200, response.text
        answer = client.post(f'/api/videos/{vid}/query', json={'question': '钥匙现在在哪里？'}).json()
        assert any(c['rule_id'] == 'conditional_containment_v1' for c in answer['inferences']) and '黑色背包' in answer['answer']
        assert client.app.state.db.raw_memory(vid)[0] == original
        with client.app.state.db.connect() as c:
            cached = json.loads(c.execute('SELECT payload FROM states WHERE run_id=? AND entity_id=?', (job['id'], event['subject_id'])).fetchone()[0])
            assert cached['container_event']['id'] == event['id']


def test_region_correction_audit_reset_and_restart(settings, sample_video):
    with TestClient(create_app(settings, MockVisionProvider()), base_url='http://localhost') as client:
        vid, job = upload_and_analyze(client, sample_video)
        original, _ = client.app.state.db.raw_memory(vid)
        source_event = original['events'][0]
        response = client.post(f'/api/videos/{vid}/regions', json={'expected_revision': 0, 'frame_ms': 0,
            'regions': [{'id': 'r_test', 'label': '测试柜格左侧', 'x1': .1, 'y1': .1, 'x2': .4, 'y2': .5}]})
        assert response.status_code == 200, response.text
        state = response.json()
        body = {'run_id': job['id'], 'expected_revision': state['revision'], 'decision': 'correct',
                'note': 'Synthetic correction only.', 'action': 'seen_at', 'target_id': 'r_test',
                'frame_ids': [original['frames'][0]['id']]}
        response = client.post(f'/api/videos/{vid}/review/{source_event["id"]}/human', json=body)
        assert response.status_code == 200, response.text
        revision = response.json()['revision']
        assert client.post(f'/api/videos/{vid}/review/{source_event["id"]}/human', json=body).status_code == 409
        memory = client.get(f'/api/videos/{vid}/events').json()
        corrected = next(e for e in memory['events'] if e['id'] == source_event['id'])
        assert corrected['target_id'] == 'r_test' and corrected['review_status'] == 'human_reviewed'
        assert client.app.state.db.raw_memory(vid)[0] == original
        assert len(client.get(f'/api/videos/{vid}/review').json()['history']) == 2
    with TestClient(create_app(settings, MockVisionProvider()), base_url='http://localhost') as client:
        memory = client.get(f'/api/videos/{vid}/events').json()
        assert memory['events'][0]['target_id'] == 'r_test'
        reset = client.post(f'/api/videos/{vid}/review/{source_event["id"]}/human', json={
            'run_id': job['id'], 'expected_revision': revision, 'decision': 'reset', 'note': 'Undo synthetic correction.'})
        assert reset.status_code == 200
        assert client.get(f'/api/videos/{vid}/events').json()['events'][0]['target_id'] == source_event['target_id']
        assert len(reset.json()['history']) == 3


@pytest.mark.parametrize('fault', ['foreign_frame', 'self_target', 'missing_target', 'stale_run'])
def test_invalid_corrections_cannot_corrupt_original(settings, sample_video, fault):
    with TestClient(create_app(settings, MockVisionProvider()), base_url='http://localhost') as client:
        vid, job = upload_and_analyze(client, sample_video)
        doc, _ = client.app.state.db.raw_memory(vid)
        event = doc['events'][0]
        body = {'run_id': job['id'], 'expected_revision': 0, 'decision': 'correct', 'note': 'Synthetic invalid correction.',
                'action': 'place_on', 'target_id': event['target_id'], 'frame_ids': [doc['frames'][0]['id']]}
        if fault == 'foreign_frame': body['frame_ids'] = ['foreign-frame']
        if fault == 'self_target': body['target_id'] = event['subject_id']
        if fault == 'missing_target': body['target_id'] = None
        if fault == 'stale_run': body['run_id'] = 'old-run'
        response = client.post(f'/api/videos/{vid}/review/{event["id"]}/human', json=body)
        assert response.status_code in {400, 409, 422}
        assert client.app.state.db.raw_memory(vid)[0] == doc
        assert client.get(f'/api/videos/{vid}/review').json()['history'] == []


class ReviewMock(MockVisionProvider):
    def verify_event(self, *, frames, instructions, **kwargs):
        assert 'JSON' in instructions  # JSON-mode service contract, checked before any real paid request.
        self.last_frames = frames
        return json.dumps({'verdict': 'refuted', 'reason': 'Synthetic fixture refutation, not visual AI.',
                           'frame_ids': [frames[-1]['id']], 'motion_visible': False, 'post_state_visible': True,
                           'contradiction_frame_ids': [frames[-1]['id']], 'subject_bbox': None}), {'test_mock': True}


def test_ai_review_records_real_source_frames_without_faking_human(settings, sample_video):
    provider = ReviewMock()
    with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
        vid, job = upload_and_analyze(client, sample_video)
        doc, _ = client.app.state.db.raw_memory(vid)
        event = doc['events'][1]
        response = client.post(f'/api/videos/{vid}/review/{event["id"]}/verify', json={'run_id': job['id'], 'expected_revision': 0})
        assert response.status_code == 200, response.text
        entry = next(e for e in response.json()['entries'] if e['original']['id'] == event['id'])
        assert entry['status'] == 'ai_refuted' and entry['human'] is None
        assert entry['machine']['is_mock'] is True
        effective = client.get(f'/api/videos/{vid}/events').json()
        assert next(e for e in effective['events'] if e['id'] == event['id'])['action'] == 'unknown'
        for frame in provider.last_frames:
            image = client.get(f'/media/frames/{vid}/{frame["id"]}')
            assert image.status_code == 200 and image.headers['content-type'].startswith('image/jpeg')
        assert client.app.state.db.raw_memory(vid)[0] == doc


def test_region_ambiguity_and_name_aliases_preserve_identity():
    box = {'x1': .1, 'y1': .1, 'x2': .2, 'y2': .2}
    regions = [{'id': 'r_a', 'x1': 0, 'y1': 0, 'x2': .4, 'y2': .4}]
    assert match_region(box, regions) == 'r_a'
    assert match_region(box, regions + [{**regions[0], 'id': 'r_b'}]) is None
    doc = {'entities': [{'id': 'one', 'label': 'black laptop', 'aliases': []}, {'id': 'two', 'label': 'white laptop', 'aliases': []}]}
    result = localized_document(doc)
    assert result['entities'][0]['label'] == '笔记本电脑'
    assert result['entities'][0]['aliases'] == ['black laptop', '电脑', '笔记本']
    assert [e['id'] for e in result['entities']] == ['one', 'two'] and doc['entities'][0]['label'] == 'black laptop'


def test_dense_review_frames_use_actual_decoded_pts(settings, sample_video, monkeypatch):
    from backend import media
    metadata = inspect_video(sample_video, settings)
    video = {'original_path': str(sample_video), 'duration_ms': metadata['duration_ms']}
    original_probe = media.command_json
    probes = []
    def probe(*args, **kwargs):
        probes.append(kwargs.get('timeout'))
        return original_probe(*args, **kwargs)
    monkeypatch.setattr(media, 'command_json', probe)
    frames = extract_review_frames(video, settings.data_dir / 'short-review', settings, 2100, 4100, 2)
    assert [f['pts_ms'] for f in frames] == [2100, 2600, 3100, 3600]
    assert all(f['kind'] == 'original_frame' for f in frames)
    tail = extract_review_frames(video, settings.data_dir / 'tail-review', settings, 9000, 12000, 1)
    assert [f['pts_ms'] for f in tail] == [9000, 10000, 11000]
    assert probes == [settings.media_timeout]  # One genuine PTS scan shared with later evidence.


def test_region_validation_rejects_out_of_bounds_and_duplicates(client, analyzed):
    vid, _ = analyzed
    region = {'id': 'r_test', 'label': 'fixture', 'x1': .1, 'y1': .1, 'x2': 1.1, 'y2': .5}
    assert client.post(f'/api/videos/{vid}/regions', json={'expected_revision': 0, 'frame_ms': 0, 'regions': [region]}).status_code == 422
    region['x2'] = .5
    assert client.post(f'/api/videos/{vid}/regions', json={'expected_revision': 0, 'frame_ms': 0, 'regions': [region, region]}).status_code == 422
    saved = client.post(f'/api/videos/{vid}/regions', json={'expected_revision': 0, 'frame_ms': 0, 'regions': [region]}).json()
    changed = {**region, 'x1': .2}
    assert client.post(f'/api/videos/{vid}/regions', json={'expected_revision': saved['revision'], 'frame_ms': 0, 'regions': [changed]}).status_code == 409


class WeakSupportMock(ReviewMock):
    def verify_event(self, *, frames, **kwargs):
        return json.dumps({'verdict': 'supported', 'reason': 'Synthetic weak support fixture.', 'frame_ids': [f['id'] for f in frames[:2]],
                           'motion_visible': True, 'post_state_visible': True, 'contradiction_frame_ids': [], 'subject_bbox': None}), {'test_mock': True}


def test_two_frames_and_model_agreement_cannot_auto_confirm_critical_action(settings, sample_video):
    with TestClient(create_app(settings, WeakSupportMock()), base_url='http://localhost') as client:
        vid, job = upload_and_analyze(client, sample_video)
        original, _ = client.app.state.db.raw_memory(vid)
        event = original['events'][1]
        response = client.post(f'/api/videos/{vid}/review/{event["id"]}/verify', json={'run_id': job['id'], 'expected_revision': 0})
        assert response.status_code == 200, response.text
        entry = next(e for e in response.json()['entries'] if e['original']['id'] == event['id'])
        assert entry['status'] == 'ai_uncertain'
        answer = client.post(f'/api/videos/{vid}/query', json={'question': '钥匙现在在哪里？'}).json()
        assert answer['inferences'] == []

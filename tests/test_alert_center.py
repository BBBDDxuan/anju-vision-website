"""A's contract tests. Synthetic events/JPEGs only; never a camera or model call."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import cv2
from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest

from backend.alert_center import AlertCenter, alert_id
from backend.alert_sources import AlertSources
from backend.phone_app import create_app
from tests.conftest import MockVisionProvider
from tests.test_hybrid_jobs import harness, finished

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'elder-safety/backend'))
from safety_backend.config import Settings as SafetySettings
from safety_backend.repository import Repository
from safety_backend.schemas import EventIn

TOKEN = 'synthetic-phone-' + 'a' * 32
USER = 'synthetic-user-' + 'b' * 32
DEVICE = 'synthetic-device-' + 'c' * 32


def image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), np.zeros((32, 32, 3), np.uint8))
    return path


def projected(key='synthetic-1', source='live_camera'):
    return {'id': alert_id(source, key), 'source': source, 'source_key': key,
            'source_event_id': key, 'event_type': 'help_requested', 'review_status': 'confirmed',
            'review_model': 'synthetic-proof-only', 'evidence': [], 'source_acknowledged': False}


@pytest.fixture
def safety(tmp_path):
    repo = Repository(SafetySettings(data_dir=tmp_path / 'elder-safety/backend', user_token=USER,
        device_tokens={'camera_001': DEVICE}, worker_enabled=False).validate())
    repo.register('camera_001', 'Synthetic camera, not connected')
    yield repo
    repo.engine.dispose()


def live(repo, key='live-test', **detail_changes):
    details = {'source_kind': 'web_camera_preview', 'simulated': False, 'cloud_images': True,
               'review_status': 'confirmed', 'review_model': 'synthetic-proof-only'}
    details.update(detail_changes)
    return repo.ingest(EventIn(event_id=key, device_id='camera_001', event_type='help_requested',
        occurred_at=datetime.now(timezone.utc), confidence=.9, details=details))


class VideoFixture:
    def __init__(self, root):
        self.path = root / 'memory.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.proof = {'candidate_id': 'candidate', 'event_type': 'help_requested',
                      'verdict': 'confirmed', 'cloud_images': True, 'model': 'synthetic-proof-only',
                      'event_ids': ['source-event']}
        self.acknowledged = False
        self.mock = False
        self.frame = image(root / 'runs/run/frames/frame.jpg')

    def list_videos(self):
        return [{'id': 'video', 'active_run': 'run'}]

    def memory(self, identifier):
        return ({'fixture_kind': 'manual_design_example' if self.mock else 'model_analysis',
                 'events': [{'id': 'source-event', 'action': 'help_gesture'}],
                 'frames': [{'id': 'frame', 'pts_ms': 1200}],
                 'evidence': [{'id': 'proof', 'frame_ids': ['frame']}]},
                {'id': 'run', 'is_mock': self.mock, 'safety': {'reviews': {'candidate': dict(self.proof)}}})

    def safety_alerts(self, identifier, *, run_id=None):
        assert run_id == 'run'
        return [{'id': 'alert-1', 'action': 'help_gesture', 'is_mock': self.mock,
                 'severity': 'urgent', 'event_ids': ['source-event'], 'evidence_ids': ['proof'],
                 'start_ms': 1200, 'end_ms': 2200, 'acknowledged': self.acknowledged}]

    def acknowledge_alert(self, video, identifier, *, run_id=None):
        assert run_id == 'run'
        self.acknowledged = True


def test_persistence_stable_ids_idempotent_ack_and_changes(tmp_path):
    center = AlertCenter(tmp_path / 'center')
    item = center.upsert(projected())
    assert center.upsert(projected())['revision'] == item['revision']
    cursor = item['revision']
    ack = center.handle(item['id'], 'ack')
    assert ack['revision'] > cursor
    assert center.handle(item['id'], 'ack')['revision'] == ack['revision']
    assert center.upsert(projected())['handling_status'] == 'acknowledged'
    restarted = AlertCenter(center.root)
    assert restarted.get(item['id'])['handling_status'] == 'acknowledged'
    changes = restarted.changes(cursor, 1)
    assert changes['alerts'][0]['id'] == item['id']
    assert changes['next_cursor'] == ack['revision']
    resolved = center.handle(item['id'], 'resolve')
    assert center.handle(item['id'], 'ack') == resolved
    assert center.claim() is None
    assert alert_id('live_camera', 'same') != alert_id('uploaded_recording', 'same')


@pytest.mark.parametrize('change', [{'review_status': 'pending'}, {'review_status': 'rejected'},
    {'cloud_images': False}, {'simulated': True}, {'review_model': ''}])
def test_unreviewed_or_mock_live_records_never_enter_formal_center(tmp_path, safety, change):
    live(safety, **change)
    center = AlertCenter(tmp_path / 'center')
    sources = AlertSources(center, SimpleNamespace(list_videos=lambda: []), safety)
    sources.sync()
    assert center.listing()['total'] == 0
    assert sources.status['live_camera']['ready']


def test_both_sources_evidence_and_original_ack_are_shared(tmp_path, safety):
    video = VideoFixture(tmp_path / 'video')
    source = live(safety)
    jpg = image(safety.s.data_dir / 'evidence/live.jpg')
    evidence = safety.add_evidence('camera_001', jpg, 'image/jpeg')
    safety.patch_event(source['event_id'], evidence_id=evidence['evidence_id'])
    center = AlertCenter(tmp_path / 'center')
    sources = AlertSources(center, video, safety)
    sources.sync()
    assert all(value['ready'] for value in sources.status.values())
    records = center.listing()['alerts']
    assert {a['source'] for a in records} == {'live_camera', 'uploaded_recording'}
    assert all(a['notification_status'] == 'skipped_history' for a in records)
    assert all(a['evidence'] for a in records)
    for record in records:
        assert center.evidence_path(record['id'], 0).read_bytes().startswith(b'\xff\xd8')
        sources.acknowledge_source(record, 'ack')
    sources.sync()
    assert all(a['handling_status'] == 'acknowledged' for a in center.listing()['alerts'])
    historical = next(a for a in records if a['source'] == 'uploaded_recording')
    assert historical['occurred_at'] is None and historical['video_offset_ms'] == 1200
    assert str(tmp_path) not in str(records)
    video.frame.unlink()
    sources.sync()
    assert center.get(historical['id'])['evidence'] == historical['evidence']


@pytest.mark.parametrize('change', [{'verdict': 'uncertain'}, {'cloud_images': False},
    {'model': ''}, {'candidate_id': 'wrong'}, {'event_ids': ['wrong']}])
def test_video_requires_actual_matching_confirmation_provenance(tmp_path, safety, change):
    video = VideoFixture(tmp_path / 'video')
    video.proof.update(change)
    center = AlertCenter(tmp_path / 'center')
    AlertSources(center, video, safety).sync()
    assert center.listing()['total'] == 0


def test_mock_video_never_becomes_formal_alert(tmp_path, safety):
    video = VideoFixture(tmp_path / 'video')
    video.mock = True
    center = AlertCenter(tmp_path / 'center')
    AlertSources(center, video, safety).sync()
    assert center.listing()['total'] == 0


def test_reanalysis_between_source_reads_keeps_proof_and_run_together(tmp_path, safety, monkeypatch):
    video = VideoFixture(tmp_path / 'video')
    monkeypatch.setattr(video, 'list_videos', lambda: [{'id': 'video', 'active_run': 'earlier-run'}])
    center = AlertCenter(tmp_path / 'center')
    sources = AlertSources(center, video, safety)
    sources.sync()
    assert sources.status['uploaded_recording']['ready']
    record = center.listing()['alerts'][0]
    assert record['source_key'] == 'video:run:alert-1' and record['run_id'] == 'run'
    assert record['evidence']


def test_gateway_two_phone_ack_auth_media_and_loopback_boundary(settings, safety, sample_video):
    requests = []
    def upstream(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer ' + USER
        return httpx.Response(200, json=[])
    app = create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(),
                     safety_repo=safety, transport=httpx.MockTransport(upstream))
    headers = {'Authorization': 'Bearer ' + TOKEN}
    with TestClient(app, base_url='http://localhost', client=('192.168.1.20', 1)) as phone:
        other = TestClient(app, base_url='http://localhost', client=('192.168.1.21', 2), headers=headers)
        assert phone.get('/api/alerts').status_code == 401
        assert phone.get('/api/connection', headers=headers).json()['notifications']['configured'] is False
        assert phone.get('/api/v1/events', headers=headers).json() == []
        assert requests
        for path in ['/api/v1/devices/register', '/api/v1/events', '/api/v1/evidence']:
            assert phone.post(path, headers=headers, json={}).status_code == 403
        assert phone.post('/api/camera/start', headers=headers).status_code == 403
        assert phone.post('/api/camera/start', headers={**headers, 'X-Forwarded-For': '127.0.0.1'}).status_code == 403
        item = app.state.alert_center.upsert(projected())
        assert other.get('/api/alerts').json()['total'] == 1
        ack = phone.post(f"/api/alerts/{item['id']}/ack", headers=headers).json()
        assert other.get(f"/api/alerts/{item['id']}").json()['handling_status'] == 'acknowledged'
        assert other.post(f"/api/alerts/{item['id']}/ack").json()['revision'] == ack['revision']
        upload = phone.post('/api/videos', files={'file': ('synthetic.mp4', sample_video.read_bytes(), 'video/mp4')}, headers=headers)
        assert upload.status_code == 201, upload.text
        video_id = upload.json()['video_id']
        assert other.get('/api/videos').json()['videos'][0]['id'] == video_id
        assert other.get('/media/videos/' + video_id).status_code == 200
        other.close()


def test_notification_failure_retry_and_ack_cancellation(tmp_path):
    center = AlertCenter(tmp_path / 'center')
    item = center.upsert(projected())
    assert center.claim()['alert_id'] == item['id']
    center.delivered(item['id'], failed=True)
    assert center.get(item['id'])['notification_status'] == 'retrying'
    assert center.listing()['total'] == 1
    center.handle(item['id'], 'ack')
    center.delivered(item['id'], failed=True)
    assert center.claim() is None


def test_history_is_not_pushed_but_new_events_queue_once(tmp_path):
    center = AlertCenter(tmp_path)
    center.upsert(projected('old'), historical=True)
    assert center.claim() is None
    new = center.upsert(projected('new'))
    center.upsert(projected('new'))
    assert center.claim()['alert_id'] == new['id']
    assert center.claim() is None


@pytest.mark.parametrize('failed', [False, True])
def test_withdrawal_during_push_cancels_without_human_ack(tmp_path, failed):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    assert center.claim()
    center.reconcile('live_camera', set())
    center.delivered(item['id'], failed=failed)
    record = center.cancel_notification(item['id'])
    assert record['review_status'] == 'withdrawn'
    assert record['handling_status'] == 'unacknowledged'
    assert record['notification_status'] == 'cancelled'
    assert center.claim() is None


def test_source_failure_does_not_retract_and_handling_writeback_retries(tmp_path, safety, monkeypatch):
    source = live(safety)
    center = AlertCenter(tmp_path / 'center')
    sources = AlertSources(center, SimpleNamespace(list_videos=lambda: []), safety)
    sources.sync()
    record = center.listing()['alerts'][0]
    original = safety.list_events
    with monkeypatch.context() as patch:
        patch.setattr(safety, 'list_events', lambda **kw: (_ for _ in ()).throw(RuntimeError('offline')))
        sources.sync()
    assert not sources.status['live_camera']['ready']
    assert center.get(record['id'])['review_status'] == 'confirmed'
    assert safety.list_events == original
    center.handle(record['id'], 'resolve')
    sources.sync()
    assert safety.get_event(source['event_id'])['state'] == 'resolved'
    safety.delete_event(source['event_id'])
    sources.sync()
    assert center.get(record['id'])['review_status'] == 'withdrawn'
    assert center.get(record['id'])['handling_status'] == 'resolved'


def test_actual_job_manager_confirmation_projects_with_evidence(harness, tmp_path, safety):
    # Actual persistence/normalization pipeline, offline scanner and scripted review.
    manager, scanner, provider, db, trace = harness([
        {}, {'event_type': 'fire_detected'}, {'event_type': 'help_requested'}, {'verdict': 'rejected'}])
    job = finished(manager, manager.start('v_offline'))
    assert job['status'] == 'completed', job
    document, _ = db.memory('v_offline')
    for frame in document['frames']:
        image(db.path.parent / 'runs' / job['id'] / 'frames' / (frame['id'] + '.jpg'))
    center = AlertCenter(tmp_path / 'center')
    sources = AlertSources(center, db, safety)
    sources.sync()
    assert sources.status['uploaded_recording']['ready']
    records = center.listing()['alerts']
    assert {r['event_type'] for r in records} == {'suspected_fall', 'fire_detected', 'help_requested'}
    assert len(records) == 3
    for record in records:
        # This scanner fixture has frames at 0.5/1.5s and trigger at 1s.
        # It has post-fall evidence, but no frame close enough to claim an
        # active help/flame moment. Never relabel its 0.5s pre-roll as active.
        assert bool(record['evidence']) == (record['event_type'] == 'suspected_fall')
    first = records[0]
    center.handle(first['id'], 'ack')
    sources.sync()
    assert next(a for a in db.safety_alerts('v_offline') if a['id'] == first['source_event_id'])['acknowledged']
    assert center.listing()['total'] == 3
    with db.connect() as c:
        c.execute('UPDATE safety_alerts SET acknowledged=0 WHERE id=?', (first['source_event_id'],))
        c.execute("UPDATE videos SET active_run='different-run' WHERE id='v_offline'")
    # An old detail page must target its original run after a reanalysis starts.
    sources.acknowledge_source(first, 'ack')
    with db.connect() as c:
        assert c.execute('SELECT acknowledged FROM safety_alerts WHERE id=?', (first['source_event_id'],)).fetchone()[0] == 1


def test_incremental_pagination_replays_ack_with_monotonic_revisions(tmp_path):
    center = AlertCenter(tmp_path)
    items = [center.upsert(projected(str(n))) for n in range(5)]
    baseline = center.listing(limit=2)
    center.handle(items[0]['id'], 'ack')
    center.upsert(projected('later'))
    cursor, changes = baseline['snapshot_cursor'], []
    while True:
        page = center.changes(cursor, 1)
        changes += page['alerts']
        cursor = page['next_cursor']
        if not page['has_more']:
            break
    assert len(changes) == 2
    assert changes[0]['handling_status'] == 'acknowledged'
    assert changes[1]['source_key'] == 'later'
    assert [c['revision'] for c in changes] == sorted(c['revision'] for c in changes)
    assert center.changes(cursor)['alerts'] == []


def eventually(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.03)
    raise AssertionError('asynchronous contract did not settle')


@pytest.mark.parametrize('invalid', [{'accepted': True, 'is_mock': True}, {'accepted': True}, {'accepted': False, 'is_mock': False}])
def test_push_dispatch_rejects_invalid_receipts_then_retries(settings, safety, invalid):
    calls = []
    async def plugin(item, *, idempotency_key):
        calls.append((item['id'], idempotency_key))
        return invalid if len(calls) == 1 else {'accepted': True, 'is_mock': False}
    app = create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(), safety_repo=safety, push_handler=plugin)
    with TestClient(app, base_url='http://localhost'):
        live(safety, key='new-after-startup')
        center = app.state.alert_center
        item = eventually(lambda: center.listing()['alerts'])[-1]
        eventually(lambda: center.get(item['id'])['notification_status'] == 'retrying')
        with center.connect() as c:
            c.execute('UPDATE outbox SET due=0 WHERE alert_id=?', (item['id'],))
        eventually(lambda: center.get(item['id'])['notification_status'] == 'accepted_by_provider')
        assert len(calls) == 2 and all(identifier == key == item['id'] for identifier, key in calls)
        assert center.listing()['total'] == 1


def test_evidence_auth_snapshot_and_upstream_failure_are_isolated(settings, safety, tmp_path):
    def unavailable(request):
        raise httpx.ConnectError('synthetic private exception', request=request)
    app = create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(), safety_repo=safety,
                     transport=httpx.MockTransport(unavailable))
    headers = {'Authorization': 'Bearer ' + TOKEN}
    with TestClient(app, base_url='http://localhost', client=('192.168.1.22', 1)) as phone:
        center = app.state.alert_center
        item = projected('evidence')
        jpg = image(tmp_path / 'source/frame.jpg')
        assert center.save_image(item['id'], 0, jpg, tmp_path / 'other') is None
        item['evidence'] = [center.save_image(item['id'], 0, jpg, tmp_path / 'source')]
        center.upsert(item)
        jpg.unlink()
        url = item['evidence'][0]['url']
        assert phone.get(url).status_code == 401
        assert phone.get(url, headers=headers).content.startswith(b'\xff\xd8')
        assert phone.get(url[:-1] + '1', headers=headers).status_code == 404
        assert phone.get(url[:-1] + '-1', headers=headers).status_code == 404
        failed = phone.get('/api/v1/events', headers=headers)
        assert failed.status_code == 503
        assert 'private exception' not in failed.text
        assert phone.get('/api/alerts', headers=headers).status_code == 200

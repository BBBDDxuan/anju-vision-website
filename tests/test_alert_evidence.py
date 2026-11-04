"""Event-phase evidence regression tests: synthetic JPEGs, no camera/cloud/GPU."""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import importlib.util

import pytest

from backend.alert_evidence import POLICY, select_frame
from backend.alert_center import AlertCenter, alert_id
from backend.alert_sources import AlertSources
from backend.errors import AppError
from tests.test_alert_center import VideoFixture, image


def candidate(kind='suspected_fall'):
    return {'id': 'candidate', 'event_type': kind, 't': 6.3,
            'frames': [{'id': 'f' + str(t), 'pts_ms': t} for t in (2300, 4500, 5000, 6000, 6300, 6500, 6800, 7000)]}


@pytest.mark.parametrize('kind,expected,phase', [
    ('suspected_fall', 6800, 'post_event'),
    ('help_requested', 6300, 'during_event'),
    ('fire_detected', 6300, 'during_event'),
])
def test_event_phase_is_not_the_earliest_review_frame(kind, expected, phase):
    frame, selected_phase = select_frame(candidate(kind), ['f2300', 'f6300'])
    assert frame['pts_ms'] == expected
    assert selected_phase == phase


def test_fall_at_eof_is_explicitly_a_trigger_fallback():
    c = candidate()
    c['frames'] = [f for f in c['frames'] if f['pts_ms'] <= 6300]
    frame, phase = select_frame(c)
    assert frame['pts_ms'] == 6300 and phase == 'trigger_fallback'


@pytest.mark.parametrize('kind', ['suspected_fall', 'help_requested', 'fire_detected'])
def test_missing_nearby_frames_do_not_present_preroll_as_event(kind):
    c = candidate(kind)
    c['frames'] = c['frames'][:3]
    assert select_frame(c) == (None, 'unavailable')


def test_help_and_fire_prefer_trigger_over_later_postroll():
    c = candidate('help_requested')
    c['frames'] = [f for f in c['frames'] if f['pts_ms'] != 6300]
    frame, phase = select_frame(c)
    assert frame['pts_ms'] == 6000 and phase == 'during_event'


def test_legacy_evidence_does_not_claim_event_phase():
    c = candidate()
    del c['t']
    frame, phase = select_frame(c, ['f2300', 'f6300'])
    assert frame['pts_ms'] == 6300 and phase == 'timing_unavailable'


class PhaseVideo(VideoFixture):
    def __init__(self, root, kind):
        super().__init__(root)
        self.candidate = candidate(kind)
        self.action = {'suspected_fall': 'fall', 'help_requested': 'help_gesture', 'fire_detected': 'flame'}[kind]
        self.proof.update(event_type=kind, frame_ids=['f2300', 'f4500', 'f5000', 'f6000', 'f6300'])
        for f in self.candidate['frames']:
            image(root / 'runs/run/frames' / (f['id'] + '.jpg'))

    def memory(self, identifier):
        document, job = super().memory(identifier)
        document.update(events=[{'id': 'source-event', 'action': self.action}],
                        frames=self.candidate['frames'],
                        evidence=[{'id': 'proof', 'frame_ids': self.proof['frame_ids']}])
        job['safety']['candidates'] = [self.candidate]
        return document, job

    def safety_alerts(self, identifier, *, run_id=None):
        item = super().safety_alerts(identifier, run_id=run_id)[0]
        item.update(action=self.action, start_ms=2300, end_ms=6300)
        return [item]


@pytest.mark.parametrize('kind,offset', [('suspected_fall', 6800), ('help_requested', 6300), ('fire_detected', 6300)])
def test_existing_alerts_migrate_without_losing_handling_or_repeating_notifications(tmp_path, kind, offset):
    db = PhaseVideo(tmp_path / 'data', kind)
    center = AlertCenter(tmp_path / 'center')
    sources = AlertSources(center, db, SimpleNamespace())
    identifier = alert_id('uploaded_recording', 'video:run:alert-1')
    # Recreate the old behavior before the fix: earliest frame, stable old URL.
    old_image = center.save_image(identifier, 0, db.frame, tmp_path / 'data/runs')
    center.upsert({'id': identifier, 'source': 'uploaded_recording', 'source_key': 'video:run:alert-1',
                   'review_status': 'confirmed', 'evidence': [{**old_image, 'video_offset_ms': 2300}]}, historical=True)
    center.handle(identifier, 'ack')
    sources.videos(False)
    record = center.get(identifier)
    assert record['handling_status'] == 'acknowledged'
    assert record['video_offset_ms'] == 2300  # Historical event range is unchanged.
    assert record['playback_offset_ms'] == offset
    selected = record['evidence'][0]
    assert selected['video_offset_ms'] == offset and selected['selection_policy'] == POLICY
    assert '?version=' in selected['url']
    version = parse_qs(urlsplit(selected['url']).query)['version'][0]
    assert center.evidence_path(identifier, 0, version).read_bytes() == db.frame.read_bytes()
    assert center.evidence_path(identifier, 0).is_file()  # Never overwrite the old snapshot.
    assert center.claim() is None
    sources.videos(False)
    assert center.get(identifier)['revision'] == record['revision']
    # Once published, source files may disappear without invalidating the image.
    for path in (tmp_path / 'data/runs/run/frames').glob('*.jpg'):
        path.unlink()
    sources.videos(False)
    assert center.get(identifier)['evidence'] == record['evidence']
    with pytest.raises(AppError):
        center.evidence_path(identifier, 0, '0' * 64)


@pytest.mark.parametrize('kind,offset', [('suspected_fall', 6800), ('help_requested', 6300), ('fire_detected', 6300)])
def test_live_worker_uses_same_event_phase_policy(kind, offset):
    script = Path(__file__).resolve().parents[1] / 'elder-safety/scripts/web-camera-worker.py'
    spec = importlib.util.spec_from_file_location('phase_worker', script)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    c = candidate(kind)
    c.update(track_id='track', confidence=.9, occurred_at='2026-11-04T00:00:00+00:00')
    for f in c['frames']:
        f['path'] = f['id'] + '.jpg'
    captured = []
    class Relay:
        def alarm(self, signal, when, keyframe=None):
            captured.append((signal.details, keyframe))
            return 'event'
    worker.apply_review_result(c, {}, {'candidate_id': c['id'], 'verdict': 'confirmed',
        'cloud_images': True, 'model': 'synthetic-only', 'frame_ids': ['f2300', 'f6300']}, Relay())
    details, path = captured[0]
    assert details['evidence_offset_ms'] == offset
    assert path == Path('f' + str(offset) + '.jpg')


def test_versioned_evidence_http_requires_phone_auth_and_valid_current_version(settings, tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from backend.phone_app import create_app
    from tests.conftest import MockVisionProvider
    from tests.test_alert_center import TOKEN, USER, DEVICE, projected
    from safety_backend.config import Settings as SafetySettings
    from safety_backend.repository import Repository
    repo = Repository(SafetySettings(data_dir=tmp_path / 'safety', user_token=USER,
        device_tokens={'camera_001': DEVICE}, worker_enabled=False).validate())
    app = create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(), safety_repo=repo)
    try:
        with TestClient(app, base_url='http://localhost', client=('192.168.1.22', 1)) as client:
            center = app.state.alert_center
            record = projected('version-test')
            jpg = image(tmp_path / 'source.jpg')
            selected = center.save_image(record['id'], 0, jpg, tmp_path, versioned=True)
            record['evidence'] = [selected]
            center.upsert(record, historical=True)
            headers = {'Authorization': 'Bearer ' + TOKEN}
            assert client.get(selected['url']).status_code == 401
            assert client.get(selected['url'], headers=headers).content == jpg.read_bytes()
            base = selected['url'].split('?')[0]
            assert client.get(base, headers=headers).content == jpg.read_bytes()
            assert client.get(base + '?version=' + '0' * 64, headers=headers).status_code == 404
            assert client.get(base + '?version=../../private', headers=headers).status_code == 422
    finally:
        repo.engine.dispose()

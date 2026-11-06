"""Offline smoke of real models through the website API; public test images only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
from fastapi.testclient import TestClient
import numpy as np
from backend.config import Settings
from backend.main import create_app


class OfflineProvider:
    name, model, is_mock = 'OFFLINE_SMOKE', 'disabled', True

    def ready(self):
        raise AssertionError('No external provider may be called')

    def extract(self, **kwargs):
        raise AssertionError('No external provider may be called')


def run(samples):
    samples = samples.resolve()
    fixtures = {name: samples / name for name in
                ('david1.jpg', 'david2.jpg', '100032540_1.jpg', '100040721_1.jpg')}
    for file in fixtures.values():
        if not file.is_file():
            raise FileNotFoundError(file)
    output = ROOT / 'runtime' / ('face-smoke-' + uuid4().hex)
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings(data_dir=output / 'data', enable_network=False)
    app = create_app(settings, OfflineProvider())
    service = app.state.face_recognition
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'data_dir': str(settings.data_dir),
              'fixtures': str(samples), 'real_models': True, 'paid_calls': 0,
              'camera_started': False, 'body_boxes': 'synthetic full-image boxes, not real tracking'}
    try:
        with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12345)) as client:
            def call(method, uri, expected=200, content=None):
                headers = {'content-type': 'image/jpeg'} if content is not None else {}
                response = client.request(method, uri, content=content, headers=headers)
                assert response.status_code == expected, (uri, response.status_code, response.text)
                return response.json()
            report['status'] = call('GET', '/api/faces/status')
            assert report['status']['ready'] is True, report['status']
            for member, name, photo in [('member-1', 'Public-sample-A', 'david1.jpg'),
                                        ('member-2', 'Public-sample-B', '100032540_1.jpg')]:
                call('POST', f'/api/faces/members/{member}/samples?name={name}&consent=true',
                     expected=201, content=fixtures[photo].read_bytes())
            report['members_after_enroll'] = call('GET', '/api/faces/members')['members']
            same = call('POST', '/api/faces/recognize', content=fixtures['david2.jpg'].read_bytes())
            assert any(f['member_id'] == 'member-1' for f in same['faces']), same
            report['same_person'] = same
            stranger = call('POST', '/api/faces/recognize', content=fixtures['100040721_1.jpg'].read_bytes())
            assert stranger['face_count'] > 0 and all(f['member_id'] is None for f in stranger['faces']), stranger
            report['different_person'] = stranger
            ok, blank = cv2.imencode('.png', np.zeros((240, 320, 3), np.uint8))
            assert ok
            response = client.post('/api/faces/recognize', content=blank.tobytes(), headers={'content-type': 'image/png'})
            assert response.status_code == 200 and response.json()['face_count'] == 0, response.text
            report['blank'] = response.json()
            candidate = {'id': 'sc_' + uuid4().hex, 'event_type': 'suspected_fall', 'track_id': 'synthetic-person',
                         'frames': [{'id': f'public-frame-{i}', 'pts_ms': i * 250, 'path': str(fixtures[photo]),
                                     'person_bboxes': {'synthetic-person': [0, 0, 1, 1]}}
                                    for i, photo in enumerate(('david1.jpg', 'david2.jpg'))]}
            report['candidate_identity'] = service.associate_candidate(candidate)
            assert report['candidate_identity']['member_id'] == 'member-1', report['candidate_identity']
            report['deleted'] = call('DELETE', '/api/faces/members/member-1/samples')
            assert report['deleted']['removed_sample_count'] == 1
            report['after_delete'] = call('POST', '/api/faces/recognize', content=fixtures['david2.jpg'].read_bytes())
            assert all(f['member_id'] is None for f in report['after_delete']['faces'])
            report['old_event_after_delete'] = service.event_identity([candidate['id']])
            assert report['old_event_after_delete']['member_id'] is None
            for member in ('member-1', 'member-2'):
                call('DELETE', '/api/household/members/' + member)
            assert call('GET', '/api/faces/members')['members'] == []
            assert call('GET', '/api/household/members')['members'] == []
            report['remaining_templates'] = len(service.gallery())
            assert report['remaining_templates'] == 0
    finally:
        # This service always points to the new smoke-only directory above.
        for profile in service.profiles.members():
            service.profiles.delete(profile['id'])
    report['passed'] = True
    file = output / 'report.json'
    file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'report': str(file), 'same_person_cosine': same['faces'][0]['similarity'],
                      'different_person_cosine': stranger['faces'][0]['similarity'],
                      'candidate_member': report['candidate_identity']['member_id'],
                      'remaining_templates': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, required=True, help='Directory containing the four named public fixtures')
    run(parser.parse_args().samples)

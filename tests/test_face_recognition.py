"""Face API, persistence and conservative attribution tests; no external requests."""
from dataclasses import replace
import json
import struct

import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest

from backend.config import Settings
from backend.errors import AppError
from backend.face_recognition import FaceRecognition, MAX_IMAGE_BYTES, MODEL_VERSION, contains_face, image_dimensions, unknown
from backend.main import create_app
from tests.conftest import MockVisionProvider


def vector(index=0):
    result = np.zeros(128, dtype='<f4')
    result[index] = 1
    return result


def face(feature=None, bbox=None):
    return {'feature': vector() if feature is None else feature,
            'bbox': bbox or [.2, .1, .4, .4], 'quality': 'usable', 'detection_score': .99}


@pytest.fixture
def faces(tmp_path, monkeypatch):
    service = FaceRecognition(Settings(data_dir=tmp_path/'data'))
    monkeypatch.setattr(service, '_features', lambda data: [face()])
    return service


def enroll(service, member='member-1', feature=None):
    if feature is not None:
        service._features = lambda data: [face(feature)]
    return service.enroll(member, 'Synthetic member', True, b'synthetic-injected-feature-not-photo')


def candidate(tmp_path, count=2, people=None):
    frames = []
    for i in range(count):
        file = tmp_path / f'frame-{i}.jpg'
        file.write_bytes(b'synthetic-frame')
        frames.append({'id': f'f{i}', 'pts_ms': i*250, 'path': str(file),
                       'person_bboxes': people if people is not None else {'p1': [0,0,1,1]}})
    return {'id': 'sc_'+'a'*32, 'track_id': 'p1', 'event_type': 'suspected_fall', 'frames': frames}


def test_consent_required_and_no_source_photos_saved(faces, tmp_path):
    with pytest.raises(AppError) as error:
        faces.enroll('member-1', 'Test', False, b'data')
    assert error.value.code == 'FACE_CONSENT_REQUIRED'
    assert faces.members() == []
    assert enroll(faces)['sample_count'] == 1
    assert list(tmp_path.rglob('*.jpg')) == []
    assert list(tmp_path.rglob('*.png')) == []
    assert len(faces.gallery()) == 1


def test_templates_persist_across_instances(faces):
    enroll(faces)
    again = FaceRecognition(faces.settings)
    assert again.members()[0]['id'] == 'member-1'
    assert np.allclose(again.gallery()[0][1], vector())


@pytest.mark.parametrize('feature,expected,reason', [
    (vector(), 'matched', 'cosine_match'),
    (vector(1), 'unknown', 'below_threshold'),
])
def test_identity_threshold(faces, feature, expected, reason):
    enroll(faces)
    result = faces._match(feature, faces.gallery())
    assert result['status'] == expected and result['reason'] == reason


def test_top_two_member_margin_and_per_member_multiple_templates(faces):
    enroll(faces)
    enroll(faces)
    assert faces._match(vector(), faces.gallery())['member_id'] == 'member-1'
    enroll(faces, 'member-2')
    result = faces._match(vector(), faces.gallery())
    assert result['status'] == 'unknown' and result['reason'] == 'ambiguous_match'


@pytest.mark.parametrize('count', [0, 2])
def test_enrollment_rejects_zero_or_multiple_faces(faces, monkeypatch, count):
    monkeypatch.setattr(faces, '_features', lambda data: [face()]*count)
    with pytest.raises(AppError) as error:
        enroll(faces)
    assert error.value.code == 'FACE_ENROLLMENT_QUALITY'
    assert faces.members() == []


def test_event_needs_two_distinct_times_and_one_member(faces, tmp_path, monkeypatch):
    enroll(faces)
    monkeypatch.setattr(faces, 'recognize', lambda data: {'faces': [{**unknown('unused'), 'status':'matched',
        'member_id':'member-1', 'similarity':.8, 'bbox':[.2,.1,.4,.4]}]})
    item = candidate(tmp_path)
    result = faces.associate_candidate(item)
    assert result['member_id'] == 'member-1'
    assert result['supporting_frame_ids'] == ['f0', 'f1']
    assert faces.event_identity([item['id']])['member_id'] == 'member-1'
    item['frames'][1]['pts_ms'] = 0
    assert faces.associate_candidate(item)['member_id'] is None


@pytest.mark.parametrize('mode', ['missing_track', 'overlapping_people', 'conflicting_members'])
def test_ambiguous_or_legacy_evidence_never_assigns(faces, tmp_path, monkeypatch, mode):
    enroll(faces)
    enroll(faces, 'member-2', vector(1))
    item = candidate(tmp_path)
    answers = iter(['member-1', 'member-2'] if mode=='conflicting_members' else ['member-1']*2)
    def recognize(data):
        return {'faces':[{'status':'matched','member_id':next(answers),'similarity':.9,'bbox':[.2,.1,.4,.4]}]}
    monkeypatch.setattr(faces, 'recognize', recognize)
    if mode == 'missing_track':
        for frame in item['frames']:
            frame.pop('person_bboxes')
        item['subject_bbox_hint'] = [0,0,1,1]  # Trigger-time hint is deliberately insufficient.
    elif mode == 'overlapping_people':
        for frame in item['frames']:
            frame['person_bboxes']['p2'] = [0,0,1,1]
    assert faces.associate_candidate(item)['member_id'] is None


def test_delete_erases_templates_and_revokes_old_event_identity(faces, monkeypatch):
    enroll(faces)
    monkeypatch.setattr(faces, '_candidate_identity', lambda item: {'status':'matched','member_id':'member-1',
        'similarity':.8,'reason':'consistent_tracked_faces','model':MODEL_VERSION})
    faces.associate_candidate({'id':'sc_test'})
    assert faces.delete('member-1')['deleted']
    assert faces.members() == [] and faces.gallery() == []
    assert faces.event_identity(['sc_test'])['reason'] == 'member_deleted'
    enroll(faces)
    assert faces.event_identity(['sc_test'])['member_id'] is None


def test_identity_failure_does_not_change_review_or_send_identity_to_cloud(tmp_path, monkeypatch):
    from tests.test_hybrid_safety import service, candidate as review_candidate
    pipeline, requests = service(tmp_path)
    def fail(item):
        raise RuntimeError('synthetic internal error with no public details')
    monkeypatch.setattr(pipeline.faces, '_candidate_identity', fail)
    result = pipeline.review(review_candidate(tmp_path), 'v1', 'j1')
    assert result['verdict'] == 'confirmed'
    assert result['face_identity']['reason'] == 'identity_unavailable'
    assert 'face_identity' not in json.dumps(requests)
    assert 'synthetic internal error' not in json.dumps(result)


def test_invalid_images_are_bounded_before_model_loading(tmp_path):
    service = FaceRecognition(Settings(data_dir=tmp_path))
    for data, code in [(b'not an image','FACE_IMAGE_INVALID'),
                       (b'x'*(MAX_IMAGE_BYTES+1),'FACE_IMAGE_TOO_LARGE'),
                       (b'\x89PNG\r\n\x1a\n'+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',100000,100000), 'FACE_IMAGE_DIMENSIONS')]:
        with pytest.raises(AppError) as error:
            service.recognize(data)
        assert error.value.code == code
    assert service.detector is None


@pytest.mark.parametrize('extension', ['.png', '.jpg'])
def test_real_models_load_from_unicode_workspace_and_handle_blank(tmp_path, extension):
    service = FaceRecognition(Settings(data_dir=tmp_path))
    assert service.status()['ready'] is True
    ok, data = cv2.imencode(extension, np.zeros((240,320,3), np.uint8))
    assert ok
    assert image_dimensions(data.tobytes()) == (320,240)
    assert service.recognize(data.tobytes())['face_count'] == 0
    with pytest.raises(AppError) as error:
        service.enroll('synthetic-no-face', 'Synthetic', True, data.tobytes())
    assert error.value.code == 'FACE_ENROLLMENT_QUALITY'
    assert service.profiles.members() == []


@pytest.mark.parametrize('orientation', range(1, 9))
def test_phone_jpeg_exif_orientation_matches_display_and_resize(tmp_path, orientation):
    pixels = np.zeros((240, 480, 3), np.uint8)
    pixels[:120, :240] = (10, 100, 240)
    pixels[120:, 240:] = (200, 40, 20)
    ok, encoded = cv2.imencode('.jpg', pixels)
    assert ok
    raw = encoded.tobytes()
    # A minimal TIFF IFD with EXIF Orientation, without an extra image dependency.
    exif = b'Exif\x00\x00II' + struct.pack('<HIH', 42, 8, 1)
    exif += struct.pack('<HHIHHI', 0x112, 3, 1, orientation, 0, 0)
    photo = raw[:2] + b'\xff\xe1' + struct.pack('>H', len(exif)+2) + exif + raw[2:]
    original = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    expected = {
        1: lambda x: x,
        2: lambda x: cv2.flip(x, 1),
        3: lambda x: cv2.rotate(x, cv2.ROTATE_180),
        4: lambda x: cv2.flip(x, 0),
        5: lambda x: cv2.transpose(x),
        6: lambda x: cv2.rotate(x, cv2.ROTATE_90_CLOCKWISE),
        7: lambda x: cv2.flip(cv2.transpose(x), -1),
        8: lambda x: cv2.rotate(x, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }[orientation](original)
    service = FaceRecognition(Settings(data_dir=tmp_path, face_max_side=320))
    height, width = expected.shape[:2]
    expected = cv2.resize(expected, (round(width*320/max(width, height)),
                                    round(height*320/max(width, height))), interpolation=cv2.INTER_AREA)
    assert np.array_equal(service._decode(photo), expected)


def test_model_integrity_failures_are_explicit(tmp_path):
    directory = tmp_path/'models'
    directory.mkdir()
    (directory/'face_detection_yunet_2026may.onnx').write_bytes(b'corrupted model')
    service = FaceRecognition(Settings(data_dir=tmp_path/'data', face_model_dir=directory))
    assert service.status()['error'] == 'FACE_MODEL_INTEGRITY'
    assert service.detector is None


def test_local_api_enroll_recognize_list_delete(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path/'data'), MockVisionProvider())
    monkeypatch.setattr(app.state.face_recognition, '_features', lambda data: [face()])
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1',12000)) as client:
        uri = '/api/faces/members/member-1/samples?name=Test'
        assert client.post(uri, content=b'data', headers={'content-type':'image/jpeg'}).status_code == 422
        response = client.post(uri+'&consent=true', content=b'data', headers={'content-type':'image/jpeg'})
        assert response.status_code == 201 and response.headers['cache-control'] == 'no-store'
        assert client.get('/api/faces/members').json()['members'][0]['sample_count'] == 1
        recognized = client.post('/api/faces/recognize', content=b'data', headers={'content-type':'image/jpeg'})
        assert recognized.json()['faces'][0]['member_id'] == 'member-1'
        assert 'feature' not in recognized.text and 'embedding' not in recognized.text
        assert client.post('/api/faces/recognize', content=b'data').status_code == 415
        oversized = client.post('/api/faces/recognize', content=b'x'*(MAX_IMAGE_BYTES+1), headers={'content-type':'image/jpeg'})
        assert oversized.status_code == 413
        assert client.delete('/api/faces/members/member-1').json()['deleted'] is True
        assert client.get('/api/faces/members').json()['members'] == []


def test_lan_uses_existing_token_and_unconfigured_lan_is_blocked(tmp_path):
    token = 'synthetic-face-api-'+'a'*32
    for configured in [False, True]:
        app = create_app(Settings(data_dir=tmp_path/str(configured), lan_token=token if configured else ''), MockVisionProvider())
        with TestClient(app, base_url='http://localhost', client=('192.168.1.24',12000)) as client:
            assert client.get('/api/faces/members').status_code == (401 if configured else 403)
            if configured:
                assert client.get('/api/faces/members', headers={'authorization':'Bearer '+token}).status_code == 200


def test_cloud_and_disabled_services_never_store_or_process_templates(tmp_path):
    for index, changes in enumerate([{'cloud_mode':True}, {'face_recognition_enabled':False}]):
        service = FaceRecognition(Settings(data_dir=tmp_path/str(index), **changes))
        assert service.status()['enabled'] is False
        with pytest.raises(AppError) as error:
            service.enroll('member-1','Test',True,b'photo')
        assert error.value.code == 'FACE_RECOGNITION_DISABLED'
        if changes.get('cloud_mode'):
            assert not service.path.exists()
        else:
            # Disabled biometrics can share a DB with local household profiles.
            with service.connect() as connection:
                assert connection.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 0
                assert connection.execute('SELECT COUNT(*) FROM members').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [{'face_match_threshold':float('nan')}, {'face_match_margin':-1},
                                     {'face_min_pixels':0}, {'face_max_side':100}])
def test_invalid_face_settings_rejected(changes):
    with pytest.raises(ValueError):
        Settings(**changes)


def test_household_profile_put_preflight_from_local_website(tmp_path):
    app = create_app(Settings(data_dir=tmp_path/'data'), MockVisionProvider())
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12000)) as client:
        response = client.options('/api/household/members/member-1', headers={
            'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'PUT',
            'Access-Control-Request-Headers': 'content-type'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
        assert 'PUT' in response.headers['access-control-allow-methods']

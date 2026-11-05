"""Synthetic household/profile/model-provisioning tests. No network, photos or cameras."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from io import BytesIO
import sqlite3
import threading

from fastapi.testclient import TestClient
import numpy as np
import pytest

from backend.errors import AppError
from backend.face_recognition import FaceRecognition, MODEL_HASHES
from backend.main import create_app
from scripts import prepare_face_models as provision
from tests.conftest import MockVisionProvider


def profile(member_id='phone-random-123', **changes):
    return {'id': member_id, 'number': 1, 'name': '合成测试成员', 'relationship': '家人',
            'notes': '合成数据\n无真实个人信息',
            'bindings': [{'device_id': 'camera-1', 'scope': 'phone-local', 'track_id': ''}], **changes}


def fake_face():
    vector = np.zeros(128, dtype='<f4')
    vector[0] = 1
    return {'feature': vector, 'bbox': [.2, .1, .4, .4], 'quality': 'usable', 'detection_score': .99}


@pytest.fixture
def api(settings):
    # Exercise the production router without initializing unrelated video databases per validation case.
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from backend.face_api import face_router
    disabled = replace(settings, face_recognition_enabled=False)
    faces = FaceRecognition(disabled)
    app = FastAPI()
    app.include_router(face_router(faces, disabled))
    @app.exception_handler(AppError)
    async def handler(request, exc):
        return JSONResponse({'error': exc.public()}, status_code=exc.status)
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12000)) as client:
        yield client, faces


def test_disabled_profile_crud_authority_and_persistence(api):
    client, faces = api
    assert client.get('/api/faces/status').json()['enabled'] is False
    assert client.get('/api/faces/status').json()['ready'] is False
    assert client.get('/api/household/members').json() == {'members': []}
    body = profile()
    response = client.put('/api/household/members/'+body['id'], json=body)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    member = response.json()['member']
    assert member == {**body, 'revision': 1, 'face_sample_count': 0}
    assert FaceRecognition(faces.settings).profiles.members() == [member]
    body.update(name='新合成姓名', revision=1)
    updated = client.put('/api/household/members/'+body['id'], json=body).json()['member']
    assert updated['revision'] == 2 and updated['name'] == '新合成姓名'
    assert client.post('/api/faces/recognize', content=b'no-photo', headers={'content-type':'image/png'}).status_code == 403
    assert client.post('/api/faces/members/'+body['id']+'/samples?name=Test&consent=true',
                       content=b'no-photo', headers={'content-type':'image/png'}).status_code == 403
    assert client.delete('/api/faces/members/'+body['id']+'/samples').status_code == 200
    assert client.delete('/api/household/members/'+body['id']).json()['deleted'] is True
    assert client.delete('/api/household/members/'+body['id']).json()['deleted'] is False
    assert client.get('/api/household/members').json() == {'members': []}
    assert FaceRecognition(faces.settings).profiles.members() == []
    with faces.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [
    {'number': True}, {'number': '1'}, {'number': 1.0}, {'number': 0}, {'number': 2147483648},
    {'name': 123}, {'name': 'x'*81}, {'name': 'bad\x00name'}, {'notes': 'x'*2001},
    {'relationship': 'x'*81}, {'extra': 'forbidden'}, {'face_sample_count': True},
    {'bindings': ['device-id']}, {'bindings': [{'device_id':'x','scope':'local','extra':1}]},
    {'bindings': [{'device_id':'','scope':'local'}]}, {'bindings': [{'device_id':'x','scope':' '} ]},
    {'bindings': [{'device_id':'x','scope':'local','track_id':123}]},
    {'bindings': [{'device_id':'x','scope':'local'}]*2}, {'bindings': [{}]*33},
    {'revision': True}, {'revision': -1}, {'revision': '1'}, {'id': 'bad/id'}, {'id':'x'*81},
])
def test_strict_profile_validation(api, changes):
    client, faces = api
    response = client.put('/api/household/members/phone-random-123', json=profile(**changes))
    assert response.status_code == 422, response.text
    assert not faces.path.exists()  # Invalid input must not even initialize persistent storage.


def test_complete_body_and_id_required_and_body_bound(api):
    client, _ = api
    assert client.put('/api/household/members/other', json=profile()).status_code == 422
    for field in ('id','number','name','relationship','notes','bindings'):
        body = profile()
        body.pop(field)
        assert client.put('/api/household/members/phone-random-123', json=body).status_code == 422
    assert client.put('/api/household/members/x', content=b'{}').status_code == 415
    assert client.put('/api/household/members/x', content=b'{', headers={'content-type':'application/json'}).status_code == 422
    assert client.put('/api/household/members/x', content=b'x'*65537,
                      headers={'content-type':'application/json'}).status_code == 413
    assert client.put('/api/household/members/phone-random-123', json=profile(name='', relationship='', notes='', bindings=[])).status_code == 200


def test_optional_revision_rejects_stale_create_update_delete_recreate(api):
    client, faces = api
    uri = '/api/household/members/phone-random-123'
    assert client.put(uri, json=profile(revision=1)).status_code == 409
    assert client.put(uri, json=profile(revision=0)).json()['member']['revision'] == 1
    assert client.put(uri, json=profile(revision=0)).status_code == 409
    assert client.put(uri, json=profile(revision=1)).json()['member']['revision'] == 2
    stale = client.put(uri, json=profile(revision=1))
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'HOUSEHOLD_REVISION_CONFLICT'
    assert client.delete(uri).json()['deleted']
    assert client.put(uri, json=profile(revision=2)).status_code == 409
    recreated = client.put(uri, json=profile(revision=0)).json()['member']
    assert recreated['revision'] > 2
    assert client.put(uri, json=profile(revision=2)).status_code == 409
    assert faces.profiles.upsert('phone-random-123', profile(name='unconditional'))['name'] == 'unconditional'


@pytest.mark.parametrize('initial', [False, True])
def test_concurrent_revision_checks_across_service_instances(settings, initial):
    first, second = FaceRecognition(settings), FaceRecognition(settings)
    if initial:
        first.profiles.upsert('phone-random-123', profile())
    gate = threading.Barrier(2)
    def update(service, name):
        gate.wait(timeout=5)
        try:
            return service.profiles.upsert('phone-random-123', profile(name=name, revision=1 if initial else 0))
        except AppError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(update, first, 'first'), pool.submit(update, second, 'second')
        results = [a.result(timeout=20), b.result(timeout=20)]
    assert sum(isinstance(item, dict) for item in results) == 1
    assert results.count('HOUSEHOLD_REVISION_CONFLICT') == 1
    assert first.profiles.members()[0]['revision'] == (2 if initial else 1)


def test_member_limit_covers_profiles_without_templates(settings):
    faces = FaceRecognition(settings)
    for i in range(32):
        faces.profiles.upsert(f'm-{i}', profile(f'm-{i}', number=i+1))
    with pytest.raises(AppError, match='32'):
        faces.profiles.upsert('extra', profile('extra'))
    faces._features = lambda data: [fake_face()]
    with pytest.raises(AppError) as exc:
        faces.enroll('extra', 'Test', True, b'synthetic')
    assert exc.value.code == 'FACE_MEMBER_LIMIT'
    assert len(faces.profiles.members()) == 32


def test_profile_name_updates_matching_and_old_event_names(settings, monkeypatch):
    faces = FaceRecognition(settings)
    faces._features = lambda data: [fake_face()]
    faces.profiles.upsert('phone-random-123', profile())
    faces.enroll('phone-random-123', 'Ignored enrollment name', True, b'synthetic')
    recognized = faces.recognize(b'synthetic')['faces'][0]
    assert recognized['name'] == profile()['name']
    monkeypatch.setattr(faces, '_candidate_identity', lambda item: dict(recognized))
    assert faces.associate_candidate({'id':'candidate-1'})['name'] == profile()['name']
    member = faces.profiles.members()[0]
    assert member['face_sample_count'] == 1 and member['revision'] == 2
    faces.profiles.upsert(member['id'], profile(name='Changed', revision=member['revision']))
    assert faces.recognize(b'synthetic')['faces'][0]['name'] == 'Changed'
    assert faces.event_identity(['candidate-1'])['name'] == 'Changed'
    assert faces.members()[0]['name'] == 'Changed'
    assert faces.profiles.delete(member['id'])['deleted']
    assert faces.event_identity(['candidate-1'])['member_id'] is None
    faces.profiles.upsert(member['id'], profile())
    faces.enroll(member['id'], 'Test', True, b'synthetic')
    assert faces.event_identity(['candidate-1'])['member_id'] is None


@pytest.mark.parametrize('suffix', ['', '/samples'])
def test_clear_templates_keeps_profile_and_revokes_old_events(settings, suffix):
    app = create_app(settings, MockVisionProvider())
    faces = app.state.face_recognition
    faces._features = lambda data: [fake_face()]
    faces.profiles.upsert('phone-random-123', profile())
    faces.enroll('phone-random-123', 'Test', True, b'synthetic')
    faces._candidate_identity = lambda item: faces.recognize(b'synthetic')['faces'][0]
    faces.associate_candidate({'id':'candidate-1'})
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1',12000)) as client:
        result = client.delete('/api/faces/members/phone-random-123'+suffix)
        assert result.status_code == 200 and result.json()['removed_sample_count'] == 1
        member = client.get('/api/household/members').json()['members'][0]
        assert member['id'] == profile()['id'] and member['face_sample_count'] == 0
        assert member['revision'] == 3
        assert faces.event_identity(['candidate-1'])['member_id'] is None
        faces.enroll(member['id'], 'Test', True, b'synthetic')
        assert faces.event_identity(['candidate-1'])['member_id'] is None


def test_enrollment_in_flight_cannot_resurrect_deleted_profile(settings):
    faces = FaceRecognition(settings)
    faces.profiles.upsert('phone-random-123', profile())
    def extract(data):
        faces.profiles.delete('phone-random-123')
        return [fake_face()]
    faces._features = extract
    with pytest.raises(AppError) as error:
        faces.enroll('phone-random-123', 'Test', True, b'synthetic')
    assert error.value.code == 'HOUSEHOLD_REVISION_CONFLICT'
    assert faces.profiles.members() == [] and faces.gallery() == []


def test_delete_during_recognition_uses_current_gallery(settings):
    faces = FaceRecognition(settings)
    faces._features = lambda data: [fake_face()]
    faces.enroll('phone-random-123', 'Test', True, b'synthetic')
    def extract(data):
        faces.profiles.delete('phone-random-123')
        return [fake_face()]
    faces._features = extract
    assert faces.recognize(b'synthetic')['faces'][0]['member_id'] is None


def test_stale_candidate_after_clear_and_reenroll_is_not_rebound(settings):
    faces = FaceRecognition(settings)
    faces._features = lambda data: [fake_face()]
    faces.enroll('phone-random-123', 'Test', True, b'synthetic')
    stale = faces.recognize(b'synthetic')['faces'][0]
    faces.delete('phone-random-123')
    faces.enroll('phone-random-123', 'Test', True, b'synthetic')
    faces._candidate_identity = lambda item: stale
    assert faces.associate_candidate({'id':'late'})['member_id'] is None
    assert faces.event_identity(['late'])['member_id'] is None


def test_legacy_face_members_migrate_without_changing_ids(settings):
    settings.data_dir.mkdir(parents=True)
    with sqlite3.connect(settings.data_dir/'face-identities.sqlite3') as c:
        c.execute('CREATE TABLE members (id TEXT PRIMARY KEY,name TEXT NOT NULL,consent_at TEXT NOT NULL)')
        c.execute('INSERT INTO members VALUES(?,?,?)', ('stable-phone-id','Legacy synthetic','2026-01-01'))
    faces = FaceRecognition(settings)
    member = faces.profiles.members()[0]
    assert member == profile('stable-phone-id', number=1, name='Legacy synthetic', relationship='', notes='', bindings=[],
                             revision=1, face_sample_count=0)
    assert FaceRecognition(settings).profiles.members() == [member]


def test_symlink_storage_is_rejected(settings, monkeypatch):
    from pathlib import Path
    original = Path.is_symlink
    target = settings.data_dir/'face-identities.sqlite3'
    monkeypatch.setattr(Path, 'is_symlink', lambda value: value == target or original(value))
    with pytest.raises(AppError) as error:
        FaceRecognition(settings)
    assert error.value.code == 'FACE_STORAGE_UNSAFE'
    assert not target.exists()


def test_household_lan_token_and_cloud_restriction(settings):
    token = 'test-only-household-token-'+'a'*32
    for enabled in (False, True):
        app = create_app(replace(settings, lan_token=token, face_recognition_enabled=enabled), MockVisionProvider())
        with TestClient(app, base_url='http://localhost', client=('192.168.1.24',12000)) as client:
            assert client.get('/api/household/members').status_code == 401
            assert client.get('/api/household/members', headers={'Authorization':'Bearer '+token}).status_code == 200
    # The standalone router must also enforce Bearer auth without main's middleware.
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from backend.face_api import face_router
    restricted = replace(settings, lan_token=token)
    app = FastAPI()
    app.include_router(face_router(FaceRecognition(restricted), restricted))
    @app.exception_handler(AppError)
    async def handler(request, exc):
        return JSONResponse({'error':exc.public()}, status_code=exc.status)
    with TestClient(app, client=('192.168.1.24',12000)) as client:
        assert client.get('/api/household/members').status_code == 401
    cloud = replace(settings, cloud_mode=True, face_recognition_enabled=False)
    app = FastAPI()
    app.include_router(face_router(FaceRecognition(cloud), cloud))
    app.add_exception_handler(AppError, handler)
    with TestClient(app, client=('127.0.0.1',12000)) as client:
        assert client.get('/api/household/members').status_code == 403


def test_provisioning_pins_match_runtime_and_no_import_network():
    assert {name: spec['sha256'] for name, spec in provision.MODEL_SPECS.items()} == MODEL_HASHES
    assert sum(spec['bytes'] for spec in provision.MODEL_SPECS.values()) == 38926091
    assert all(provision.UPSTREAM_COMMIT in spec['url'] for spec in provision.MODEL_SPECS.values())


@pytest.fixture
def tiny_model(monkeypatch, tmp_path):
    data = b'synthetic-model-test-bytes'
    spec = {'url':'https://example.invalid/never-called', 'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(provision, 'MODEL_SPECS', {'tiny.onnx':spec})
    return tmp_path/'models', data


def test_provision_atomic_verified_and_idempotent(tiny_model, monkeypatch):
    directory, data = tiny_model
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        assert not (directory/'tiny.onnx').exists()
        return BytesIO(data)
    monkeypatch.setattr(provision, 'urlopen', fetch)
    assert provision.prepare_models(directory)[0]['status'] == 'downloaded_and_verified'
    assert (directory/'tiny.onnx').read_bytes() == data
    assert provision.prepare_models(directory)[0]['status'] == 'verified'
    assert len(calls) == 1 and list(directory.glob('*.part')) == []


@pytest.mark.parametrize('download', [b'wrong', b'x'*25, b'x'*100])
def test_provision_bad_download_preserves_existing_and_cleans_temp(tiny_model, monkeypatch, download):
    directory, _ = tiny_model
    directory.mkdir()
    target = directory/'tiny.onnx'
    target.write_bytes(b'existing-do-not-destroy')
    monkeypatch.setattr(provision, 'urlopen', lambda *a, **k: BytesIO(download))
    with pytest.raises(ValueError):
        provision.prepare_models(directory)
    assert target.read_bytes() == b'existing-do-not-destroy'
    assert list(directory.iterdir()) == [target]


def test_provision_check_never_downloads(tiny_model, monkeypatch):
    directory, data = tiny_model
    def forbidden(*args, **kwargs):
        raise AssertionError('Network is forbidden')
    monkeypatch.setattr(provision, 'urlopen', forbidden)
    with pytest.raises(ValueError):
        provision.prepare_models(directory, check_only=True)
    assert not directory.exists()
    directory.mkdir()
    (directory/'tiny.onnx').write_bytes(data)
    assert provision.prepare_models(directory, check_only=True)[0]['status'] == 'verified'


def test_provision_network_failure_cleans_temp(tiny_model, monkeypatch):
    directory, _ = tiny_model
    def fail(*args, **kwargs):
        raise OSError('synthetic network failure')
    monkeypatch.setattr(provision, 'urlopen', fail)
    with pytest.raises(OSError):
        provision.prepare_models(directory)
    assert list(directory.iterdir()) == []


def test_full_flutter_tojson_count_is_readonly_and_delete_checks_revision(api):
    client, _ = api
    uri = '/api/household/members/phone-random-123'
    response = client.put(uri, json=profile(face_sample_count=987, revision=0))
    assert response.status_code == 200, response.text
    member = response.json()['member']
    assert member['face_sample_count'] == 0
    assert client.put(uri, json=member).status_code == 200
    assert client.delete(uri+'?revision=1').status_code == 409
    assert client.get('/api/household/members').json()['members'][0]['revision'] == 2
    assert client.delete(uri+'?revision=2').json()['deleted'] is True
    assert client.delete(uri+'?revision=2').status_code == 409
    assert client.delete(uri+'?revision=0').json()['deleted'] is False


def test_main_reuses_hybrid_face_instance_and_mounts_disabled_profiles(settings):
    app = create_app(replace(settings, face_recognition_enabled=False), MockVisionProvider())
    assert app.state.face_recognition is app.state.manager.safety.faces
    assert app.state.live_camera.safety is app.state.manager.safety
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12000)) as client:
        assert client.get('/api/household/members').json() == {'members': []}
        assert client.put('/api/household/members/phone-random-123', json=profile()).status_code == 200
        assert client.get('/api/faces/status').json()['enabled'] is False


def test_missing_models_do_not_disable_household_profiles(settings):
    app = create_app(replace(settings, face_recognition_enabled=True, face_model_dir=settings.data_dir/'missing-models'), MockVisionProvider())
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1',12000)) as client:
        status = client.get('/api/faces/status')
        assert status.status_code == 200
        assert status.json()['enabled'] is True and status.json()['ready'] is False
        assert status.json()['error'] == 'FACE_MODEL_MISSING'
        assert status.json()['max_image_bytes'] == 5242880
        assert status.json()['max_members'] == 32 and status.json()['max_samples_per_member'] == 10
        assert client.put('/api/household/members/phone-random-123', json=profile()).status_code == 200
        assert client.get('/api/household/members').json()['members'][0]['face_sample_count'] == 0


def test_clear_during_first_enrollment_cancels_inflight_write(settings):
    faces = FaceRecognition(settings)
    faces.profiles.upsert('phone-random-123', profile())
    def extract(data):
        assert faces.delete('phone-random-123')['removed_sample_count'] == 0
        return [fake_face()]
    faces._features = extract
    with pytest.raises(AppError) as error:
        faces.enroll('phone-random-123', 'Test', True, b'synthetic')
    assert error.value.code == 'HOUSEHOLD_REVISION_CONFLICT'
    assert faces.profiles.members()[0]['face_sample_count'] == 0
    assert faces.gallery() == []


def test_relative_private_storage_checks_parent_aliases(settings, tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda value: value == tmp_path or original(value))
    with pytest.raises(AppError) as error:
        FaceRecognition(replace(settings, data_dir=Path('relative-private-data')))
    assert error.value.code == 'FACE_STORAGE_UNSAFE'
    assert not (tmp_path/'relative-private-data').exists()
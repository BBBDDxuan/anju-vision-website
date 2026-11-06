"""Phone monitoring contract; synthetic worker only, no camera/cloud calls."""
from dataclasses import replace
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import camera_monitoring
from backend import main as video_main
from backend.errors import AppError
from backend.phone_app import create_app
from backend.live_camera import LiveCamera
from tests.conftest import MockVisionProvider
from tests.test_alert_center import safety  # Reuse the isolated device repository fixture.

TOKEN = 'synthetic-monitor-' + 'm' * 32
HEADERS = {'Authorization': 'Bearer ' + TOKEN}
CONNECTION = dict(host='192.168.137.39', port=554, username='local@user',
                  password='synthetic:# /?', path='stream1&channel=2')


@pytest.fixture
def phone(settings, safety, tmp_path, monkeypatch):
    monkeypatch.setattr(video_main, 'LiveCamera', lambda safety: LiveCamera(tmp_path, safety))
    app = create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(), safety_repo=safety)
    camera = app.state.live_camera
    camera.safety = SimpleNamespace(ready=lambda: None, enabled=True, model='synthetic')
    camera.starts = camera.stops = 0
    env = tmp_path / 'elder-safety/.env'
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text('SAFETY_DEVICE_ID=synthetic-camera\nSAFETY_DEVICE_TOKEN=synthetic-device\n')

    def start():
        if camera.process is None:
            camera.starts += 1
            camera.process = SimpleNamespace(poll=lambda: None, stdin=None)
            camera.info['state'] = 'running'
            camera.received = time.monotonic()
        return camera.status()

    def stop():
        camera.stops += 1
        camera.process = None
        camera.info['state'] = 'stopped'
        return camera.status()

    monkeypatch.setattr(camera, 'start', start)
    monkeypatch.setattr(camera, 'stop', stop)
    monkeypatch.setattr(camera_monitoring, 'probe_camera', lambda *args: None)
    monkeypatch.setattr(camera_monitoring, 'probe_stream', lambda *args: None)
    with TestClient(app, base_url='http://localhost', client=('192.168.1.20', 40000)) as client:
        yield client, camera, tmp_path / 'runtime/camera-connection.json'


def test_gateway_auth_and_origin_keep_local_preview_private(phone):
    client, camera, profile = phone
    for method, path in [('GET', ''), ('POST', ''), ('POST', '/start'), ('POST', '/stop')]:
        response = client.request(method, '/api/monitoring/camera' + path, json=CONNECTION)
        assert response.status_code == 401
    assert client.get('/api/camera/status', headers=HEADERS).status_code == 403
    assert client.post('/api/monitoring/camera', json=CONNECTION,
                       headers={**HEADERS, 'Origin': 'https://untrusted.example'}).status_code == 403
    assert not profile.exists() and camera.starts == 0
    connection = client.get('/api/connection', headers=HEADERS).json()
    assert connection['camera_control'] == 'authenticated_shared_backend'


@pytest.mark.parametrize('change', [dict(host='127.0.0.1'), dict(host='8.8.8.8'),
    dict(host='169.254.169.254'), dict(host='evil.example'), dict(port=0), dict(port='554'),
    dict(path='../secret'), dict(path='rtsp://other'), dict(path='/'), dict(password='')])
def test_invalid_connection_does_not_mutate_or_echo_secrets(phone, change):
    client, camera, profile = phone
    response = client.post('/api/monitoring/camera', headers=HEADERS, json={**CONNECTION, **change})
    assert response.status_code == 422
    assert CONNECTION['password'] not in response.text and CONNECTION['username'] not in response.text
    assert not profile.exists() and camera.starts == 0


def test_profile_encoding_shared_idempotence_stop_and_resume(phone):
    client, camera, profile = phone
    response = client.post('/api/monitoring/camera', headers=HEADERS, json=CONNECTION)
    assert response.status_code == 200
    data = response.json()
    assert data['monitoring'] is True and data['lifetime'] == 'backend'
    assert data['state'] == 'running' and data['configured'] is True
    assert data['camera'] == {'host': CONNECTION['host'], 'port': 554}
    private = json.loads(profile.read_text(encoding='utf-8'))
    assert private['rtsp_url'] == ('rtsp://local%40user:synthetic%3A%23%20%2F%3F@'
                                   '192.168.137.39:554/stream1&channel=2')
    for secret in (CONNECTION['password'], CONNECTION['username'], private['rtsp_url'], str(profile)):
        assert secret not in response.text
    # A second phone connects to the same profile without restarting the worker.
    assert client.post('/api/monitoring/camera', headers=HEADERS, json=CONNECTION).status_code == 200
    assert camera.starts == 1
    assert client.get('/api/monitoring/camera', headers=HEADERS).json()['monitoring'] is True
    assert client.post('/api/monitoring/camera/stop', headers=HEADERS).json()['monitoring'] is False
    assert profile.is_file()
    assert client.post('/api/monitoring/camera/start', headers=HEADERS).json()['monitoring'] is True
    assert camera.starts == 2


def test_unreachable_replacement_preserves_active_profile(phone, monkeypatch):
    client, camera, profile = phone
    client.post('/api/monitoring/camera', headers=HEADERS, json=CONNECTION)
    before, stops = profile.read_bytes(), camera.stops
    def fail(*args):
        raise AppError('CAMERA_UNREACHABLE', 'synthetic network failure', 503)
    monkeypatch.setattr(camera_monitoring, 'probe_camera', fail)
    response = client.post('/api/monitoring/camera', headers=HEADERS,
                           json={**CONNECTION, 'host': '10.0.0.9'})
    assert response.status_code == 503
    assert profile.read_bytes() == before and camera.stops == stops
    assert camera.starts == 1 and camera.process is not None


def test_missing_alert_identity_does_not_claim_monitoring_started(phone):
    client, camera, profile = phone
    (camera.root / 'elder-safety/.env').write_text('')
    response = client.post('/api/monitoring/camera', headers=HEADERS, json=CONNECTION)
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'CAMERA_ALERT_RELAY_MISSING'
    assert not profile.exists() and camera.starts == 0


def test_failed_runtime_restores_previous_profile(phone, monkeypatch):
    client, camera, profile = phone
    client.post('/api/monitoring/camera', headers=HEADERS, json=CONNECTION)
    before = profile.read_bytes()
    def fail():
        raise AppError('CAMERA_RUNTIME_MISSING', 'synthetic missing runtime', 503)
    monkeypatch.setattr(camera, 'start', fail)
    assert client.post('/api/monitoring/camera', headers=HEADERS,
                       json={**CONNECTION, 'host': '10.0.0.9'}).status_code == 503
    assert json.loads(profile.read_bytes()) == json.loads(before)


def test_backend_heartbeat_does_not_require_any_http_status_requests(tmp_path):
    camera = LiveCamera(tmp_path)
    worker = SimpleNamespace(poll=lambda: None)
    camera.process = worker
    messages = []
    stopped = threading.Event()
    def send(message):
        messages.append(message)
        stopped.set()
    camera._send = send
    camera.status()
    assert messages == []
    camera._keep_alive(worker, stopped)
    assert messages == [b'ping']
    camera.process = None
    camera.review_pool.shutdown()


def test_rejected_credentials_preserve_working_camera(phone, monkeypatch):
    client, camera, profile = phone
    client.post('/api/monitoring/camera', headers=HEADERS, json=CONNECTION)
    before, stops = profile.read_bytes(), camera.stops
    def fail(*args):
        raise AppError('CAMERA_AUTH_FAILED', '摄像头本地账号或密码不正确。', 422)
    monkeypatch.setattr(camera_monitoring, 'probe_stream', fail)
    response = client.post('/api/monitoring/camera', headers=HEADERS,
                           json={**CONNECTION, 'password': 'synthetic-wrong'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'CAMERA_AUTH_FAILED'
    assert profile.read_bytes() == before and camera.stops == stops
    assert 'synthetic-wrong' not in response.text
    assert client.post('/api/monitoring/camera/start', headers=HEADERS).status_code == 200
    camera.received = None  # A stale worker must revalidate the saved credentials.
    assert client.post('/api/monitoring/camera/start', headers=HEADERS).status_code == 422
    assert camera.starts == 1


@pytest.mark.parametrize('diagnostic,code', [
    (b'401 Unauthorized rtsp://secret:password@camera', 'CAMERA_AUTH_FAILED'),
    (b'403 Forbidden', 'CAMERA_AUTH_FAILED'),
    (b'404 Not Found', 'CAMERA_STREAM_NOT_FOUND'),
    (b'connection reset rtsp://secret:password@camera', 'CAMERA_STREAM_UNAVAILABLE'),
])
def test_probe_stream_classifies_errors_without_native_diagnostics(monkeypatch, diagnostic, code):
    monkeypatch.setattr(camera_monitoring.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout=b'', stderr=diagnostic))
    with pytest.raises(AppError) as caught:
        camera_monitoring.probe_stream('rtsp://secret:password@192.168.1.3/stream1', 'ffprobe')
    assert caught.value.code == code
    assert 'secret' not in str(caught.value) and 'password' not in str(caught.value)


@pytest.mark.parametrize('output,valid', [
    (b'{"streams":[{"codec_type":"video","width":640,"height":360}]}', True),
    (b'{"streams":[]}', False), (b'invalid', False),
])
def test_probe_requires_video_metadata(monkeypatch, output, valid):
    monkeypatch.setattr(camera_monitoring.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=output, stderr=b''))
    if valid:
        camera_monitoring.probe_stream('rtsp://synthetic', 'ffprobe')
    else:
        with pytest.raises(AppError, match='视频轨道'):
            camera_monitoring.probe_stream('rtsp://synthetic', 'ffprobe')


def test_probe_timeout_and_missing_tool_do_not_echo_url(monkeypatch):
    for error, code in [
        (camera_monitoring.subprocess.TimeoutExpired(['secret'], 8), 'CAMERA_STREAM_TIMEOUT'),
        (FileNotFoundError('secret'), 'CAMERA_PROBE_MISSING'),
    ]:
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr(camera_monitoring.subprocess, 'run', fail)
        with pytest.raises(AppError) as caught:
            camera_monitoring.probe_stream('rtsp://secret', 'ffprobe')
        assert caught.value.code == code and 'secret' not in str(caught.value)

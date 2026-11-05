from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from tests.conftest import MockVisionProvider


TOKEN = 'test-only-phone-token-' + 'x' * 32


@pytest.mark.parametrize('path,method', [
    ('/api/health', 'GET'), ('/api/videos', 'GET'),
    ('/api/uploads', 'POST'), ('/media/videos/private', 'GET'), ('/workspace', 'GET'),
])
def test_phone_requires_token_for_api_media_and_workspace(settings, path, method):
    with TestClient(create_app(replace(settings, lan_token=TOKEN), MockVisionProvider()),
                    base_url='http://localhost', client=('192.168.1.20', 50000)) as client:
        for headers in ({}, {'Authorization': 'Bearer wrong'}, {'X-Forwarded-For': '127.0.0.1'},
                        {'Authorization': 'Bearer 非ASCII'}):
            # HTTPX rejects non-ASCII header strings, so send UTF-8 bytes for the malformed input.
            headers = {k: v.encode('utf8') for k, v in headers.items()}
            response = client.request(method, path, headers=headers)
            assert response.status_code == 401
            assert response.headers['www-authenticate'] == 'Bearer'
        assert TOKEN not in response.text


def test_phone_token_allows_data_but_not_camera_controls(settings):
    with TestClient(create_app(replace(settings, lan_token=TOKEN), MockVisionProvider()),
                    base_url='http://localhost', client=('192.168.1.20', 50000)) as client:
        headers = {'Authorization': f'Bearer {TOKEN}'}
        assert client.get('/api/health', headers=headers).status_code == 200
        assert client.get('/api/videos', headers=headers).status_code == 200
        assert client.get('/workspace', headers=headers).status_code == 200
        assert client.get('/api/camera/status', headers=headers).status_code == 403
        assert client.post('/api/camera/start', headers=headers).status_code == 403


@pytest.mark.parametrize('peer', ['127.0.0.1', '::1'])
def test_local_webpage_stays_available(settings, peer):
    with TestClient(create_app(replace(settings, lan_token=TOKEN), MockVisionProvider()),
                    base_url='http://localhost', client=(peer, 50000)) as client:
        assert client.get('/').status_code == 200
        assert client.get('/api/health').status_code == 200


def test_invalid_phone_token_fails_before_listening(settings):
    with pytest.raises(ValueError):
        replace(settings, lan_token='short')
    with pytest.raises(ValueError):
        replace(settings, lan_token=TOKEN, cloud_mode=True)


@pytest.mark.parametrize('host', ['192.168.137.1', '10.1.1.11', '172.16.11.202'])
def test_explicit_pc_hosts_still_require_phone_auth(settings, host):
    configured = replace(settings, lan_token=TOKEN,
                         allowed_hosts=('localhost', '192.168.137.1', '10.1.1.11', '172.16.11.202'))
    with TestClient(create_app(configured, MockVisionProvider()),
                    base_url=f'http://{host}:8000', client=('192.168.137.57', 50000)) as client:
        assert client.get('/api/videos').status_code == 401
        assert client.get('/api/videos', headers={'Authorization': f'Bearer {TOKEN}'}).status_code == 200
        assert client.post('/api/camera/start', headers={'Authorization': f'Bearer {TOKEN}'}).status_code == 403


def test_phone_host_diagnostics_do_not_log_secrets(settings, caplog):
    with TestClient(create_app(replace(settings, lan_token=TOKEN), MockVisionProvider()),
                    base_url='http://unlisted.example', client=('192.168.137.57', 50000)) as client:
        response = client.get('/api/videos?secret=must-not-be-logged',
                              headers={'Authorization': f'Bearer {TOKEN}'})
        assert response.status_code == 400
    assert 'host=unlisted.example' in caplog.text
    assert 'peer=192.168.137.57' in caplog.text
    assert TOKEN not in caplog.text
    assert 'must-not-be-logged' not in caplog.text


def test_phone_launcher_rejects_nonlocal_and_public_addresses():
    from unittest.mock import patch
    from scripts.phone_server import private_address
    with pytest.raises(ValueError):
        private_address('8.8.8.8')
    with patch('scripts.phone_server.socket.socket') as socket_factory:
        socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError('not local')
        with pytest.raises(OSError):
            private_address('192.168.200.200')


def test_upload_finalization_logs_real_media_error_without_credentials(settings, caplog):
    with TestClient(create_app(replace(settings, lan_token=TOKEN), MockVisionProvider()),
                    base_url='http://localhost', client=('192.168.137.57', 50000)) as client:
        headers = {'Authorization': f'Bearer {TOKEN}'}
        session = client.post('/api/uploads', json={'filename': 'invalid.mp4', 'size': 4}, headers=headers).json()
        identifier = session['upload_id']
        assert client.post(f'/api/uploads/{identifier}/chunks?offset=0', content=b'nope', headers=headers).status_code == 200
        response = client.post(f'/api/uploads/{identifier}/complete', headers=headers)
        assert response.status_code == 400
        assert response.json()['error']['code'] == 'INVALID_MP4'
        assert client.get('/api/videos', headers=headers).json()['videos'] == []
    assert 'endpoint=/api/uploads/{upload_id}/complete; code=INVALID_MP4; status=400' in caplog.text
    assert TOKEN not in caplog.text

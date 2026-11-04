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

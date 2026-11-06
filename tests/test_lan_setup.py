from fastapi.testclient import TestClient

from backend import lan_setup
from backend.phone_app import create_app
from tests.conftest import MockVisionProvider
from tests.test_alert_center import safety

TOKEN = 'synthetic-pairing-test-token-' + 'p' * 32
LOCAL = ('127.0.0.1', 23456)
PHONE = ('192.168.1.32', 23457)


def test_pairing_local_only_one_time_and_discovery_authenticated(settings, monkeypatch, safety):
    from dataclasses import replace

    monkeypatch.setenv('ANJU_PHONE_ADDRESS', '192.168.1.12')
    monkeypatch.setattr(lan_setup, 'discover_cameras', lambda: [
        {'host': '192.168.1.55', 'port': 554, 'username': 'admin', 'path': 'stream1'}])
    app = create_app(replace(settings, lan_token=TOKEN, allowed_hosts=('localhost',)), MockVisionProvider(), safety_repo=safety)
    pc = TestClient(app, client=LOCAL, base_url='http://localhost')
    phone = TestClient(app, client=PHONE, base_url='http://localhost')
    assert phone.post('/api/lan/pair').status_code == 401
    assert phone.post('/api/lan/pair', headers={'Authorization': f'Bearer {TOKEN}'}).status_code == 403
    assert phone.get('/api/lan/cameras').status_code == 401
    assert pc.post('/api/lan/pair', headers={'Origin': 'https://unrelated.test'}).status_code == 403
    issued = pc.post('/api/lan/pair')
    assert issued.status_code == 200
    assert issued.headers['Cache-Control'] == 'no-store'
    qr = issued.json()
    assert TOKEN not in issued.text
    assert qr['origin'] == 'http://192.168.1.12:8000'
    path = '/api/lan/pair/exchange'
    assert phone.post(path, json={'code': 'A' * 43}).status_code == 403
    response = phone.post(path, json={'code': qr['code']})
    assert response.status_code == 200
    assert response.json() == {'token': TOKEN}
    assert response.headers['Cache-Control'] == 'no-store'
    assert phone.post(path, json={'code': qr['code']}).status_code == 403
    assert phone.get('/api/lan/cameras', headers={'Authorization': f'Bearer {TOKEN}'}).json()['cameras'][0]['host'] == '192.168.1.55'

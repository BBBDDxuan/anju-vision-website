from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib

import pytest
from fastapi.testclient import TestClient

from backend.accounts import COOKIE
from backend.phone_app import create_app
from tests.conftest import MockVisionProvider
from tests.test_alert_center import projected, safety

TOKEN = 'synthetic-legacy-' + 'x' * 32
PC = ('127.0.0.1', 12000)
PHONE = ('192.168.1.25', 12000)
ORIGIN = 'http://localhost'
CREDENTIALS = {'username': 'owner', 'password': 'test-123'}
FAMILY = {**CREDENTIALS, 'display_name': '管理员', 'household_name': '测试家庭'}


def gateway(settings, safety, monkeypatch, demo=False):
    monkeypatch.setenv('ANJU_ACCOUNTS', '1')
    monkeypatch.setenv('ANJU_ACCOUNT_DEMO_HTTP', '1' if demo else '0')
    monkeypatch.setenv('ANJU_ACCOUNT_ORIGIN', 'http://192.168.1.9:8000')
    return create_app(replace(settings, lan_token=TOKEN, allowed_hosts=('localhost',)),
                      MockVisionProvider(), safety_repo=safety)


def test_family_login_invitation_and_revocation(settings, safety, monkeypatch):
    app = gateway(settings, safety, monkeypatch, demo=True)
    web = TestClient(app, client=PC, base_url=ORIGIN)
    phone = TestClient(app, client=PHONE, base_url=ORIGIN)
    old = {'Authorization': 'Bearer '+TOKEN}

    assert web.get('/api/auth/status').json()['setup_allowed'] is True
    for path in ['/api/videos', '/api/alerts', '/api/household/members', '/media/videos/synthetic']:
        assert web.get(path).status_code == 401
        assert phone.get(path, headers=old).status_code == 401
    assert phone.post('/api/auth/setup', json=FAMILY).status_code == 403
    assert web.post('/api/auth/setup', json=FAMILY, headers={'Origin': 'http://other.invalid'}).status_code == 403
    assert web.post('/api/auth/setup', json={**FAMILY, 'password': 'seven12'}).status_code == 422
    setup = web.post('/api/auth/setup', json=FAMILY, headers={'Origin': ORIGIN})
    assert setup.status_code == 200
    assert setup.json()['user']['role'] == 'admin'
    assert 'token' not in setup.json()
    assert COOKIE in setup.cookies and 'HttpOnly' in setup.headers['set-cookie']
    assert web.get('/api/auth/status').json()['setup_allowed'] is False
    assert web.post('/api/auth/setup', json=FAMILY).status_code == 409
    assert web.get('/api/videos').status_code == 200
    assert web.post('/api/auth/invite', json={}).status_code == 403
    invited = web.post('/api/auth/invite', json={'contact_role':'backup'}, headers={'Origin': ORIGIN}).json()
    assert invited['kind'] == 'anju-invite-v1'
    code = {'code':invited['code']}
    assert phone.post('/api/auth/invite-info', json=code).json()['household_name'] == '测试家庭'
    member = {'username':'family', 'password':'test-234', 'client':'app'}
    assert phone.post('/api/auth/register', json={**code, **member, 'display_name':'家人', 'password':'seven12'}).status_code == 422
    registered = phone.post('/api/auth/register', json={**code, **member, 'display_name':'家人', 'role':'admin'})
    assert registered.status_code == 200
    user = registered.json()['user']
    assert user['role'] == 'member' and 'contact_role' not in user
    assert phone.post('/api/auth/invite-info', json=code).status_code == 403
    headers = {'Authorization':'Bearer '+registered.json()['token']}
    assert phone.get('/api/auth/me', headers=headers).json()['user'] == user
    assert phone.get('/api/household/members', headers=headers).status_code == 200
    assert phone.get('/api/auth/users', headers=headers).status_code == 403
    directory = phone.get('/api/auth/family-members', headers=headers).json()['members']
    assert {row['display_name'] for row in directory} == {'管理员', '家人'}
    assert all(set(row) == {'id', 'display_name', 'role'} for row in directory)
    alert = app.state.alert_center.upsert(projected())
    for client, credentials in [(web, {}), (phone, headers)]:
        assert client.get('/api/alerts/'+alert['id'], headers=credentials).status_code == 200
    assert phone.post('/api/monitoring/camera/stop', headers=headers).status_code == 403
    assert phone.delete('/api/videos/synthetic', headers=headers).status_code == 403
    assert phone.post('/api/lan/pair/exchange', json=code, headers=old).status_code == 401
    assert phone.post('/api/auth/logout', headers=headers).status_code == 200
    assert phone.get('/api/videos', headers=headers).status_code == 401
    signed = phone.post('/api/auth/login', json=member)
    assert signed.status_code == 200
    headers = {'Authorization':'Bearer '+signed.json()['token']}
    assert web.post('/api/auth/users/'+user['id']+'/revoke', headers={'Origin':ORIGIN}).status_code == 200
    assert phone.get('/api/videos', headers=headers).status_code == 401
    assert phone.post('/api/auth/login', json=member).status_code == 401
    assert [row['role'] for row in web.get('/api/auth/family-members').json()['members']] == ['admin']


def test_insecure_lan_requires_explicit_demo(settings, safety, monkeypatch):
    app = gateway(settings, safety, monkeypatch)
    phone = TestClient(app, client=PHONE, base_url=ORIGIN)
    assert phone.get('/api/auth/status').json()['insecure_lan_demo'] is False
    response = phone.post('/api/auth/login', json=CREDENTIALS)
    assert response.status_code == 403 and response.json()['error']['code'] == 'HTTPS_REQUIRED'
    https = TestClient(app, client=PC, base_url='https://localhost')
    response = https.post('/api/auth/setup', json=FAMILY)
    assert response.status_code == 200
    assert 'Secure' in response.headers['set-cookie']


@pytest.mark.parametrize(('origin', 'code'), [
    ('', 'QR_ADDRESS_MISSING'),
    ('http://192.168.1.9:8000', 'HTTPS_REQUIRED'),
    ('https://127.0.0.1:8000', 'QR_ADDRESS_LOCAL_ONLY'),
    ('https://192.168.1.9:8000/api/auth', 'QR_ADDRESS_INVALID'),
    ('https://192.168.1.9:8000/', None),
])
def test_account_qr_validates_advertised_address(settings, safety, monkeypatch, origin, code):
    app = gateway(settings, safety, monkeypatch)
    app.state.accounts.origin = origin.rstrip('/')
    web = TestClient(app, client=PC, base_url=ORIGIN)
    assert web.post('/api/auth/setup', json=FAMILY).status_code == 200
    results = [
        web.get('/api/auth/connection-code'),
        web.post('/api/auth/invite', json={}, headers={'Origin': ORIGIN}),
    ]
    if code:
        for response in results:
            assert response.json()['error']['code'] == code
            assert 'start-phone.ps1' in response.json()['error']['message']
        with app.state.accounts.db() as db:
            assert db.execute('SELECT COUNT(*) FROM invitations').fetchone()[0] == 0
    else:
        assert [response.json()['kind'] for response in results] == ['anju-server-v1', 'anju-invite-v1']
        assert all(response.json()['origin'] == origin.rstrip('/') for response in results)


def test_pairing_public_ca_and_scanned_payloads(settings, safety, monkeypatch, tmp_path):
    ca = b'-----BEGIN CERTIFICATE-----\nY2E=\n-----END CERTIFICATE-----\n'
    leaf = b'-----BEGIN CERTIFICATE-----\nbGVhZg==\n-----END CERTIFICATE-----\n'
    ca_file, tls_file = tmp_path / 'public-ca.pem', tmp_path / 'server.pem'
    ca_file.write_bytes(ca)
    tls_file.write_bytes(leaf + ca)
    monkeypatch.setenv('ANJU_ACCOUNT_CA_PEM', str(ca_file))
    monkeypatch.setenv('ANJU_ACCOUNT_TLS_CERT', str(tls_file))
    app = gateway(settings, safety, monkeypatch)
    app.state.accounts.origin = 'https://192.168.1.9:8000'
    origin = 'https://localhost'
    web = TestClient(app, client=PC, base_url=origin)
    phone = TestClient(app, client=PHONE, base_url=origin)
    assert web.post('/api/auth/setup', json=FAMILY).status_code == 200
    cert = phone.get('/api/auth/pair-ca')
    assert cert.status_code == 200 and cert.content == ca
    assert cert.headers['cache-control'] == 'no-store'
    assert TestClient(app, client=PHONE, base_url=ORIGIN).get('/api/auth/pair-ca').status_code == 404
    for response in [
        web.get('/api/auth/connection-code'),
        web.post('/api/auth/invite', json={}, headers={'Origin': origin}),
        web.post('/api/auth/login-code', json={}, headers={'Origin': origin}),
    ]:
        assert response.status_code == 200
        assert response.json()['origin'] == app.state.accounts.origin
        assert response.json()['ca_sha256'] == hashlib.sha256(ca).hexdigest()
        assert response.json()['tls_sha256'] == hashlib.sha256(b'leaf').hexdigest()
    ca_file.write_bytes(ca + b'-----BEGIN PRIVATE KEY-----\nYQ==\n-----END PRIVATE KEY-----\n')
    with pytest.raises(ValueError, match='only public PEM'):
        gateway(settings, safety, monkeypatch)


def test_login_qr_only_redeems_once_for_original_user(settings, safety, monkeypatch):
    app = gateway(settings, safety, monkeypatch)
    app.state.accounts.origin = 'https://192.168.1.9:8000'
    origin = 'https://localhost'
    web = TestClient(app, client=PC, base_url=origin)
    phone = TestClient(app, client=PHONE, base_url=origin)
    headers = {'Origin': origin}
    create = '/api/auth/login-code'
    exchange = create + '/exchange'
    assert web.post(create, json={}, headers=headers).status_code == 401
    admin = web.post('/api/auth/setup', json=FAMILY).json()['user']
    assert web.post(create, json={}, headers={'Origin': 'https://other.example'}).status_code == 403
    old = web.post(create, json={}, headers=headers).json()['code']
    code = web.post(create, json={}, headers=headers).json()['code']
    assert phone.post(exchange, json={'code': old}).status_code == 403
    assert TestClient(app, client=PHONE, base_url=ORIGIN).post(exchange, json={'code': code}).status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: phone.post(exchange, json={'code': code}), range(2)))
    assert sorted(r.status_code for r in replies) == [200, 403]
    redeemed = next(r for r in replies if r.status_code == 200)
    assert redeemed.json()['user'] == admin
    token = redeemed.json()['token']
    assert phone.post(create, json={}, headers={'Authorization': 'Bearer ' + token}).status_code == 403
    assert phone.get('/api/auth/me', headers={'Authorization': 'Bearer ' + token}).json()['user'] == admin
    expired = web.post(create, json={}, headers=headers).json()['code']
    with app.state.accounts.db() as db:
        db.execute('UPDATE qr_logins SET expires=0')
    assert phone.post(exchange, json={'code': expired}).status_code == 403
    revoked = web.post(create, json={}, headers=headers).json()['code']
    assert web.post('/api/auth/logout', json={}, headers=headers).status_code == 200
    assert phone.post(exchange, json={'code': revoked}).status_code == 403

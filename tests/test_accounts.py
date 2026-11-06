from dataclasses import replace

from fastapi.testclient import TestClient

from backend.accounts import COOKIE
from backend.phone_app import create_app
from tests.conftest import MockVisionProvider
from tests.test_alert_center import projected, safety

TOKEN = 'synthetic-legacy-' + 'x' * 32
PC = ('127.0.0.1', 12000)
PHONE = ('192.168.1.25', 12000)
ORIGIN = 'http://localhost'
CREDENTIALS = {'username': 'owner', 'password': 'synthetic-password-1'}
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
    member = {'username':'family', 'password':'synthetic-password-2', 'client':'app'}
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

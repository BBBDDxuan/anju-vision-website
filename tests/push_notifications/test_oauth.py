"""Service-account signing with temporary synthetic keys and offline HTTP only."""
import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from backend.push_notifications.publisher import PushError, ServiceAccountToken


@pytest.fixture
def synthetic_service_account(tmp_path):
    # This dependency is only needed for optional real FCM signing tests.
    pytest.importorskip('google.oauth2.service_account')
    rsa = pytest.importorskip('cryptography.hazmat.primitives.asymmetric.rsa')
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    filename = tmp_path / 'synthetic-service-account.json'
    filename.write_text(json.dumps({
        'type': 'service_account', 'project_id': 'synthetic-project',
        'private_key_id': 'synthetic-key', 'private_key': private,
        'client_email': 'synthetic@synthetic-project.iam.gserviceaccount.com',
        'token_uri': 'https://credential-supplied.invalid/not-allowed',
    }), encoding='utf8')
    return filename, public


def test_signed_oauth_assertion_uses_fixed_endpoint_and_shared_cache(synthetic_service_account):
    from google.auth import jwt
    filename, public = synthetic_service_account
    requests = []
    def handler(request):
        requests.append(request)
        assert str(request.url) == 'https://oauth2.googleapis.com/token'
        form = parse_qs(request.content.decode())
        assert form['grant_type'] == ['urn:ietf:params:oauth:grant-type:jwt-bearer']
        claims = jwt.decode(form['assertion'][0], certs=public, audience='https://oauth2.googleapis.com/token')
        assert claims['scope'] == 'https://www.googleapis.com/auth/firebase.messaging'
        assert claims['iss'] == 'synthetic@synthetic-project.iam.gserviceaccount.com'
        return httpx.Response(200, json={'access_token': 'synthetic-access-token', 'expires_in': 3600})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            credentials = ServiceAccountToken(filename, client)
            assert await asyncio.gather(credentials.access_token(), credentials.access_token()) == ['synthetic-access-token'] * 2
            assert await credentials.access_token() == 'synthetic-access-token'
            assert len(requests) == 1
    asyncio.run(run())


def test_oauth_failure_is_sanitized_and_never_cached(synthetic_service_account):
    filename, _ = synthetic_service_account
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={'error': 'synthetic-private-do-not-leak'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            credentials = ServiceAccountToken(filename, client)
            for _ in range(2):
                with pytest.raises(PushError, match='^FCM_AUTH_FAILED$'):
                    await credentials.access_token()
            assert len(requests) == 2 and credentials.cached is None
    asyncio.run(run())


def test_invalid_service_account_never_leaks_file_contents(tmp_path):
    filename = tmp_path / 'invalid-service-account.json'
    filename.write_text('{"private_key":"synthetic-private-do-not-leak"}', encoding='utf8')
    with pytest.raises(PushError, match='^FCM_CREDENTIALS_UNAVAILABLE$'):
        ServiceAccountToken(filename, None)

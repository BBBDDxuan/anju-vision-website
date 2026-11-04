"""Synthetic offline provider tests. No actual FCM calls or phone delivery."""
import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
import sys

import httpx
import pytest

from backend.push_notifications.publisher import (
    AcceptanceLedger, AlertPublisher, FcmTransport, PushError, Recipient, SafetyRecipients,
)


def alert(**changes):
    return {"id": "a_" + "1" * 40, "source": "live_camera", "event_type": "suspected_fall",
            "review_status": "confirmed", "handling_status": "unacknowledged",
            "notification_status": "pending", **changes}


class Recipients:
    def __init__(self, enabled=True, tokens=None):
        self.enabled = enabled
        self.tokens = tokens if tokens is not None else [Recipient("one", "synthetic-private-one", "android"), Recipient("two", "synthetic-private-two", "android")]

    def snapshot(self):
        return self.enabled, self.tokens


class Transport:
    def __init__(self, failing=()):
        self.calls = []
        self.failing = set(failing)

    async def send(self, recipient, item):
        self.calls.append(recipient.token_id)
        if recipient.token_id in self.failing:
            raise RuntimeError("synthetic-private-token-do-not-leak")


def publish(publisher, item=None):
    item = item or alert()
    return asyncio.run(publisher.publish(item, idempotency_key=item['id']))


def test_all_devices_and_persistent_duplicate_suppression(tmp_path):
    transport, recipients = Transport(), Recipients()
    ledger = AcceptanceLedger(tmp_path / 'receipts.sqlite3')
    receipt = publish(AlertPublisher(recipients, transport, ledger))
    assert receipt == {"accepted": True, "is_mock": False, "recipient_count": 2}
    assert sorted(transport.calls) == ['one', 'two']
    restarted = AlertPublisher(recipients, transport, AcceptanceLedger(tmp_path / 'receipts.sqlite3'))
    publish(restarted)
    assert sorted(transport.calls) == ['one', 'two']
    assert b'synthetic-private' not in (tmp_path / 'receipts.sqlite3').read_bytes()


def test_partial_failure_does_not_block_others_or_repeat_accepted_device(tmp_path):
    transport = Transport(failing=['one'])
    publisher = AlertPublisher(Recipients(), transport, AcceptanceLedger(tmp_path / 'receipt.db'))
    with pytest.raises(PushError, match='^RECIPIENTS_NOT_ALL_ACCEPTED$') as failure:
        publish(publisher)
    assert 'private' not in str(failure.value)
    assert sorted(transport.calls) == ['one', 'two']
    transport.failing.clear()
    publish(publisher)
    assert transport.calls.count('one') == 2 and transport.calls.count('two') == 1


@pytest.mark.parametrize('changes', [
    {'review_status': 'pending'}, {'review_status': 'withdrawn'}, {'review_status': 'failed'},
    {'handling_status': 'acknowledged'}, {'handling_status': 'resolved'},
    {'source': 'unknown'}, {'notification_status': 'skipped_history'},
    {'notification_status': 'cancelled'}, {'id': 'raw-camera-candidate'},
])
def test_only_unhandled_confirmed_unified_alerts_send(tmp_path, changes):
    transport = Transport()
    publisher = AlertPublisher(Recipients(), transport, AcceptanceLedger(tmp_path / 'r.db'))
    with pytest.raises(PushError, match='ALERT_NOT_ELIGIBLE'):
        publish(publisher, alert(**changes))
    assert transport.calls == []


@pytest.mark.parametrize('enabled,tokens,code', [(False, None, 'NOTIFICATIONS_DISABLED'), (True, [], 'NO_REGISTERED_RECIPIENTS')])
def test_disabled_and_no_recipients_are_not_fake_acceptance(tmp_path, enabled, tokens, code):
    transport = Transport()
    with pytest.raises(PushError, match=code):
        publish(AlertPublisher(Recipients(enabled, tokens), transport, AcceptanceLedger(tmp_path / 'r.db')))
    assert transport.calls == []


def test_cancellation_and_deadline_keep_pending_device_unaccepted(tmp_path):
    class Waiting:
        async def send(self, recipient, item):
            await asyncio.sleep(5)
    async def run():
        ledger = AcceptanceLedger(tmp_path / 'r.db')
        publisher = AlertPublisher(Recipients(), Waiting(), ledger, attempt_timeout=.05)
        with pytest.raises(PushError, match='PUSH_ATTEMPT_FAILED'):
            await publisher.publish(alert(), idempotency_key=alert()['id'])
        assert not ledger.accepted(alert()['id'], Recipients().tokens[0].key)
        task = asyncio.create_task(publisher.publish(alert(), idempotency_key=alert()['id']))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())


def test_idempotency_key_must_match_unified_id(tmp_path):
    publisher = AlertPublisher(Recipients(), Transport(), AcceptanceLedger(tmp_path / 'r.db'))
    with pytest.raises(PushError, match='ALERT_NOT_ELIGIBLE'):
        asyncio.run(publisher.publish(alert(), idempotency_key='other'))


class Credentials:
    project = 'synthetic-project'
    async def access_token(self):
        return 'synthetic-oauth-never-live'


def test_actual_http_v1_payload_is_private_tagged_and_uses_unified_id():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'name': 'projects/synthetic-project/messages/offline-fixture'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await FcmTransport(client, Credentials(), scope='synthetic-backend|default').send(Recipients().tokens[0], alert())
    asyncio.run(run())
    import json
    body = json.loads(requests[0].content)['message']
    assert str(requests[0].url) == 'https://fcm.googleapis.com/v1/projects/synthetic-project/messages:send'
    assert body['data']['alert_id'] == body['data']['event_id'] == alert()['id']
    assert body['data']['anju_type'] == 'unified_alert'
    assert not ({'from', 'message_type'} & body['data'].keys())
    assert not any(k.startswith(('google.', 'gcm.')) for k in body['data'])
    assert body['data']['scope'] == 'synthetic-backend|default'
    assert body['android']['notification']['channel_id'] == 'elder_safety_alerts_v1'
    assert body['android']['notification']['tag'] == alert()['id']
    assert body['android']['notification']['visibility'] == 'PRIVATE'
    assert 'evidence' not in body['data'] and 'playback_url' not in body['data']


@pytest.mark.parametrize('status,payload', [(401, {'error': 'synthetic-private-token'}), (200, {}), (200, {'name': 'other-project/messages/1'}), (503, {})])
def test_provider_failure_and_invalid_receipts_are_sanitized(status, payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload))) as client:
            with pytest.raises(PushError) as failure:
                await FcmTransport(client, Credentials(), scope='synthetic-backend|default').send(Recipients().tokens[0], alert())
            assert 'private' not in str(failure.value)
    asyncio.run(run())


def test_real_repository_supports_two_installs_rotation_and_scoped_delete(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'safety'))
    from safety_backend.config import Settings
    from safety_backend.repository import Repository
    from safety_backend.schemas import PushIn, Preferences
    settings = Settings(data_dir=tmp_path / 'safety', user_token='u'*32, device_tokens={'camera': 'd'*32}).validate()
    repository = Repository(settings)
    try:
        one = repository.register_token(PushIn(token='synthetic-install-1', platform='android'))
        two = repository.register_token(PushIn(token='synthetic-install-2', platform='android'))
        assert one != two
        assert repository.register_token(PushIn(token='synthetic-install-2', platform='android')) == two
        enabled, recipients = SafetyRecipients(repository).snapshot()
        assert enabled and len(recipients) == 2
        repository.delete_token(one['token_id'])
        replacement = repository.register_token(PushIn(token='synthetic-install-1-rotated', platform='android'))
        assert len(SafetyRecipients(repository).snapshot()[1]) == 2
        repository.delete_token(replacement['token_id'])
        assert [r.token_id for r in SafetyRecipients(repository).snapshot()[1]] == [two['token_id']]
        repository.save_preferences(Preferences(notifications_enabled=False))
        assert SafetyRecipients(repository).snapshot() == (False, [])
    finally:
        repository.engine.dispose()


def test_mock_recipients_filtered_even_when_registered(tmp_path):
    class Repo:
        def get_preferences(self):
            return {'notifications_enabled': True, 'contacts': [{'contact_id': 'primary'}, {'contact_id': 'backup'}]}
        def contact_tokens(self, contact):
            return [{'platform': 'mock', 'token_id': 'x', 'token': 'synthetic-mock'},
                    {'platform': 'android', 'token_id': 'a', 'token': 'synthetic-same-token'}]
    enabled, recipients = SafetyRecipients(Repo()).snapshot()
    assert enabled and len(recipients) == 1


@pytest.mark.parametrize('enabled,credentials,origin,expected', [
    ('false', '', '', 'FCM_NOT_ENABLED'),
    ('true', '', '', 'FCM_CREDENTIALS_UNAVAILABLE'),
    ('true', 'synthetic-not-opened.json', '', 'FCM_ORIGIN_REQUIRED'),
    ('true', 'synthetic-not-opened.json', 'https://private:secret@example.invalid', 'FCM_ORIGIN_REQUIRED'),
    ('true', 'synthetic-not-opened.json', 'http://example.invalid/api', 'FCM_ORIGIN_REQUIRED'),
    ('true', 'synthetic-not-opened.json', 'http://example.invalid:bad', 'FCM_ORIGIN_REQUIRED'),
    ('true', 'synthetic-not-opened.json', 'http://[malformed', 'FCM_ORIGIN_REQUIRED'),
])
def test_plugin_configuration_fails_before_network(monkeypatch, enabled, credentials, origin, expected):
    from backend.push_notifications.publisher import publish_alert
    monkeypatch.setenv('APP_FCM_ENABLED', enabled)
    monkeypatch.setenv('APP_FCM_CREDENTIALS', credentials)
    monkeypatch.setenv('APP_FCM_ORIGIN', origin)
    with pytest.raises(PushError, match=expected):
        asyncio.run(publish_alert(alert(), idempotency_key=alert()['id']))

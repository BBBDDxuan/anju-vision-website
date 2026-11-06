"""Offline contact escalation contracts: synthetic data, fake clock, no devices/network."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.alert_center import AlertCenter, alert_id
from backend.phone_app import create_app
from backend.push_notifications.publisher import (
    AcceptanceLedger, AlertPublisher, FcmTransport, PushError, SafetyRecipients, publish_alert,
)
from tests.conftest import MockVisionProvider
from tests.test_alert_center import safety, eventually, live, TOKEN


def preferences(**changes):
    return {'notifications_enabled': True, 'escalation_seconds': 120,
            'contacts': [{'contact_id': 'primary'}, {'contact_id': 'backup'}], **changes}


def projected(key='fresh', **changes):
    source = changes.pop('source', 'live_camera')
    return {'id': alert_id(source, key), 'source': source, 'source_key': key,
            'source_event_id': key, 'review_status': 'confirmed', 'severity': 'critical',
            'event_type': 'help_requested', 'evidence': [], **changes}


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=1800000000.0)
    monkeypatch.setattr('backend.alert_center.time', SimpleNamespace(time=lambda: value.now))
    return value


def done(center, job, *, failed=False):
    return center.delivered(job['alert_id'], contact_stage=job['contact_stage'], failed=failed)


def stages(center, identifier):
    return center.get(identifier)['contact_escalation']


@pytest.mark.parametrize('source', ['live_camera', 'uploaded_recording'])
def test_primary_first_and_backup_at_creation_deadline_not_first_dispatch(tmp_path, clock, source):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected(source=source))
    clock.now += 37
    primary = center.claim(preferences=preferences())
    assert primary['contact_stage'] == 'primary'
    state = stages(center, item['id'])
    assert datetime.fromisoformat(state['backup_due_at']).timestamp() == 1800000120
    assert datetime.fromisoformat(state['primary_scheduled_at']).timestamp() == 1800000000
    done(center, primary)
    clock.now = 1800000119.999
    assert center.claim(preferences=preferences()) is None
    clock.now = 1800000120
    backup = center.claim(preferences=preferences())
    assert backup['contact_stage'] == 'backup'
    done(center, backup)
    clock.now += 10000
    assert center.claim(preferences=preferences()) is None
    assert center.get(item['id'])['notification_status'] == 'accepted_by_provider'


def test_restart_lease_recovery_retry_and_preference_edits_do_not_move_deadline(tmp_path, clock):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    done(center, center.claim(preferences=preferences()))
    original = stages(center, item['id'])
    restarted = AlertCenter(tmp_path)
    clock.now += 119
    assert restarted.claim(preferences=preferences(escalation_seconds=900)) is None
    assert stages(restarted, item['id']) == original
    clock.now += 1
    backup = restarted.claim(preferences=preferences(escalation_seconds=900))
    assert backup['contact_stage'] == 'backup'
    crashed = AlertCenter(tmp_path)
    clock.now += 29.999
    assert crashed.claim(preferences=preferences()) is None
    clock.now += .001
    recovered = crashed.claim(preferences=preferences())
    assert recovered['contact_stage'] == 'backup'
    done(crashed, recovered, failed=True)
    with crashed.connect() as c:
        row = c.execute('SELECT * FROM contact_escalations').fetchone()
        assert row['first_due'] == 1800000120 and row['due'] == clock.now + 2
    clock.now += 2
    done(crashed, crashed.claim(preferences=preferences()))
    assert stages(crashed, item['id'])['backup_status'] == 'accepted_by_provider'
    assert crashed.claim(preferences=preferences()) is None


@pytest.mark.parametrize('action', ['ack', 'resolve', 'source_ack', 'source_resolve'])
@pytest.mark.parametrize('failed', [False, True])
def test_any_confirmation_before_due_cancels_all_pending_stages(tmp_path, clock, action, failed):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    done(center, center.claim(preferences=preferences()), failed=failed)
    clock.now += 1
    if action.startswith('source_'):
        center.upsert(projected(**{'source_acknowledged' if action == 'source_ack' else 'source_resolved': True}))
    else:
        center.handle(item['id'], action)
    clock.now += 1000
    assert AlertCenter(tmp_path).claim(preferences=preferences()) is None
    state = stages(center, item['id'])
    assert state['backup_status'] == 'cancelled'
    assert state['primary_status'] == ('cancelled' if failed else 'accepted_by_provider')
    with center.connect() as c:
        for table in ['outbox', 'contact_escalations']:
            assert not c.execute(f"SELECT 1 FROM {table} WHERE state IN ('pending','sending')").fetchone()


@pytest.mark.parametrize('stage', ['primary', 'backup'])
@pytest.mark.parametrize('failed', [False, True])
def test_ack_during_attempt_is_durable_and_late_receipt_does_not_revive_jobs(tmp_path, clock, stage, failed):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    primary = center.claim(preferences=preferences())
    if stage == 'backup':
        done(center, primary)
        clock.now += 120
        job = center.claim(preferences=preferences())
    else:
        job = primary
    cancelled = center.handle(item['id'], 'ack')
    assert done(center, job, failed=failed) == cancelled
    clock.now += 1000
    assert AlertCenter(tmp_path).claim(preferences=preferences()) is None


def test_cancel_before_first_claim_never_plans_backup(tmp_path, clock):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    center.handle(item['id'], 'ack')
    clock.now += 1000
    assert center.claim(preferences=preferences()) is None
    with center.connect() as c:
        assert not c.execute('SELECT 1 FROM contact_escalations').fetchone()


@pytest.mark.parametrize('severity', ['critical', 'high', 'urgent'])
@pytest.mark.parametrize('kind', ['suspected_fall', 'fire_detected', 'smoke_detected', 'help_requested'])
def test_important_high_priority_types_get_backup(tmp_path, clock, severity, kind):
    center = AlertCenter(tmp_path)
    center.upsert(projected(severity=severity, event_type=kind))
    done(center, center.claim(preferences=preferences()))
    clock.now += 120
    assert center.claim(preferences=preferences())['contact_stage'] == 'backup'


@pytest.mark.parametrize('changes', [
    {'severity': 'warning'}, {'severity': 'info'}, {'severity': None},
    {'event_type': 'device_offline'}, {'event_type': 'uncertain'}, {'event_type': 'unknown'},
])
def test_other_alerts_only_schedule_primary(tmp_path, clock, changes):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected(**changes))
    done(center, center.claim(preferences=preferences()))
    assert stages(center, item['id'])['backup_status'] == 'not_required'
    clock.now += 10000
    assert center.claim(preferences=preferences()) is None


@pytest.mark.parametrize('review', ['pending', 'rejected', 'withdrawn'])
def test_unconfirmed_alerts_do_not_enqueue(tmp_path, clock, review):
    center = AlertCenter(tmp_path)
    center.upsert(projected(review_status=review))
    assert center.claim(preferences=preferences()) is None


def test_history_remains_history_after_updates_and_restart(tmp_path, clock):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected(), historical=True)
    clock.now += 500
    center.upsert(projected(title='updated historical source'))
    assert AlertCenter(tmp_path).claim(preferences=preferences()) is None
    assert center.get(item['id'])['notification_status'] == 'skipped_history'


@pytest.mark.parametrize('old_state', ['pending', 'sending', 'sent', 'cancelled'])
def test_additive_migration_preserves_old_rows_without_retroactive_backup(tmp_path, clock, old_state):
    item = projected()
    item.update(handling_status='unacknowledged', created_at='2026-01-01T00:00:00+00:00',
                notification_status='accepted_by_provider' if old_state == 'sent' else 'pending')
    path = tmp_path / 'alerts.sqlite3'
    with sqlite3.connect(path) as c:
        c.executescript('''CREATE TABLE alerts(id TEXT PRIMARY KEY,source TEXT,source_key TEXT,body TEXT);
                          CREATE TABLE outbox(alert_id TEXT PRIMARY KEY,attempts INTEGER NOT NULL DEFAULT 0,
                          due REAL NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'pending');''')
        c.execute('INSERT INTO alerts VALUES(?,?,?,?)', (item['id'], item['source'], item['source_key'], json.dumps(item)))
        c.execute('INSERT INTO outbox VALUES(?,?,?,?)', (item['id'], 3, 42, old_state))
    center = AlertCenter(tmp_path)
    assert center.get(item['id']) == item
    with center.connect() as c:
        old = dict(c.execute('SELECT * FROM outbox').fetchone())
        assert old['state'] == old_state and old['attempts'] == 3 and old['due'] == 42
        assert old['scheduled_at'] is None
    job = center.claim(preferences=preferences())
    if old_state in {'pending', 'sending'}:
        assert job['contact_stage'] == 'primary'
        done(center, job)
    else:
        assert job is None
    center.upsert(projected(title='updated old record'))
    clock.now += 10000
    assert AlertCenter(tmp_path).claim(preferences=preferences()) is None
    with center.connect() as c:
        assert not c.execute('SELECT 1 FROM contact_escalations').fetchone()


def test_source_updates_do_not_reset_due_or_reenqueue_sent_stages(tmp_path, clock):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    done(center, center.claim(preferences=preferences()))
    state = stages(center, item['id'])
    clock.now += 20
    center.upsert(projected(title='new evidence', contact_escalation={'backup_due_at': 'spoofed'}))
    assert stages(center, item['id']) == state
    assert center.claim(preferences=preferences(escalation_seconds=5)) is None
    clock.now += 100
    done(center, center.claim(preferences=preferences()))
    sent = center.get(item['id'])
    assert center.upsert(projected(title='new evidence'))['revision'] == sent['revision']
    center.upsert(projected(title='later evidence'))
    clock.now += 10000
    assert AlertCenter(tmp_path).claim(preferences=preferences()) is None


def test_disabled_preferences_hold_jobs_and_keep_creation_anchor(tmp_path, clock):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    assert center.claim(preferences=preferences(notifications_enabled=False)) is None
    clock.now += 200
    assert center.claim(preferences=preferences(notifications_enabled=False)) is None
    primary = center.claim(preferences=preferences())
    assert primary['contact_stage'] == 'primary'
    done(center, primary)
    assert datetime.fromisoformat(stages(center, item['id'])['backup_due_at']).timestamp() == 1800000120
    assert center.claim(preferences=preferences())['contact_stage'] == 'backup'


def test_missing_backup_role_does_not_fan_out_or_backfill_later(tmp_path, clock):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    done(center, center.claim(preferences=preferences(contacts=[{'contact_id': 'primary'}])))
    assert stages(center, item['id'])['backup_status'] == 'not_required'
    clock.now += 500
    assert center.claim(preferences=preferences()) is None


class ContactRepo:
    def __init__(self, *, tokens=None, prefs=None):
        self.prefs = prefs if prefs is not None else preferences()
        self.tokens = tokens if tokens is not None else {
            role: [{'token_id': role, 'token': 'synthetic-token-' + role, 'platform': 'android'}]
            for role in ['primary', 'backup']}

    def get_preferences(self):
        return self.prefs

    def contact_tokens(self, role):
        return self.tokens.get(role, [])


class OfflineTransport:
    def __init__(self, failing=()):
        self.calls = []
        self.failing = set(failing)

    async def send(self, recipient, item):
        self.calls.append((recipient.token_id, item.get('contact_stage', 'primary')))
        if recipient.token_id in self.failing:
            raise RuntimeError('synthetic offline failure')


def push(publisher, item, stage='primary'):
    return asyncio.run(publisher.publish({**item, 'contact_stage': stage}, idempotency_key=item['id']))


def test_primary_failure_retries_only_primary_and_backup_waits_until_original_deadline(tmp_path, clock):
    center = AlertCenter(tmp_path / 'center')
    item = center.upsert(projected())
    repo, transport = ContactRepo(), OfflineTransport(failing={'primary'})
    publisher = AlertPublisher(SafetyRecipients(repo), transport, AcceptanceLedger(tmp_path / 'acceptance.db'))
    for offset in [0, 119]:
        clock.now = 1800000000 + offset
        job = center.claim(preferences=repo.prefs)
        assert job['contact_stage'] == 'primary'
        with pytest.raises(PushError, match='RECIPIENTS_NOT_ALL_ACCEPTED'):
            push(publisher, center.get(item['id']), job['contact_stage'])
        done(center, job, failed=True)
    assert transport.calls == [('primary', 'primary'), ('primary', 'primary')]
    clock.now = 1800000120
    backup = center.claim(preferences=repo.prefs)
    assert backup['contact_stage'] == 'backup'
    push(publisher, center.get(item['id']), 'backup')
    done(center, backup)
    assert transport.calls[-1] == ('backup', 'backup')
    assert stages(center, item['id'])['primary_status'] == 'retrying'
    assert stages(center, item['id'])['backup_status'] == 'accepted_by_provider'


def test_role_filtering_partial_acceptance_and_restart_ledger(tmp_path):
    repo = ContactRepo()
    repo.tokens['primary'].append({'token_id': 'primary-two', 'token': 'synthetic-token-two', 'platform': 'ios'})
    recipients, transport = SafetyRecipients(repo), OfflineTransport(failing={'primary-two'})
    ledger = AcceptanceLedger(tmp_path / 'ledger.db')
    publisher = AlertPublisher(recipients, transport, ledger)
    item = {'handling_status': 'unacknowledged', 'notification_status': 'pending', **projected()}
    with pytest.raises(PushError, match='RECIPIENTS_NOT_ALL_ACCEPTED'):
        push(publisher, item)
    assert sorted(transport.calls) == [('primary', 'primary'), ('primary-two', 'primary')]
    transport.failing.clear()
    restarted = AlertPublisher(recipients, transport, AcceptanceLedger(tmp_path / 'ledger.db'))
    push(restarted, item)
    push(restarted, item, 'backup')
    push(restarted, item, 'backup')
    assert transport.calls.count(('primary', 'primary')) == 1
    assert transport.calls.count(('primary-two', 'primary')) == 2
    assert transport.calls.count(('backup', 'backup')) == 1
    with ledger.connect() as c:
        keys = {row[0] for row in c.execute('SELECT alert_id FROM acceptance')}
        assert {item['id'] + ':primary', item['id'] + ':backup'} <= keys
    assert b'synthetic-token' not in (tmp_path / 'ledger.db').read_bytes()


def test_same_token_in_both_roles_or_rotated_to_backup_does_not_spam(tmp_path):
    repo, transport = ContactRepo(), OfflineTransport()
    repo.tokens['backup'] = [dict(repo.tokens['primary'][0], token_id='same-on-backup')]
    ledger = AcceptanceLedger(tmp_path / 'ledger.db')
    publisher = AlertPublisher(SafetyRecipients(repo), transport, ledger)
    item = {'handling_status': 'unacknowledged', 'notification_status': 'pending', **projected()}
    push(publisher, item)
    publisher = AlertPublisher(SafetyRecipients(repo), transport, AcceptanceLedger(tmp_path / 'ledger.db'))
    push(publisher, item, 'backup')
    assert transport.calls == [('primary', 'primary')]
    # Different alerts still notify the same device.
    push(publisher, {**item, 'id': alert_id('live_camera', 'other')}, 'backup')
    assert len(transport.calls) == 2


@pytest.mark.parametrize('stage', ['primary', 'backup'])
def test_disabled_or_removed_role_is_not_provider_acceptance(tmp_path, stage):
    repo, transport = ContactRepo(), OfflineTransport()
    publisher = AlertPublisher(SafetyRecipients(repo), transport, AcceptanceLedger(tmp_path / 'ledger.db'))
    item = {'handling_status': 'unacknowledged', 'notification_status': 'pending', **projected()}
    repo.prefs['notifications_enabled'] = False
    with pytest.raises(PushError, match='NOTIFICATIONS_DISABLED'):
        push(publisher, item, stage)
    repo.prefs = preferences(contacts=[{'contact_id': 'backup' if stage == 'primary' else 'primary'}])
    with pytest.raises(PushError, match='NO_REGISTERED_RECIPIENTS'):
        push(publisher, item, stage)
    assert transport.calls == []


@pytest.mark.parametrize('stage', ['primary', 'backup'])
def test_fcm_disabled_fails_closed_for_both_stages(monkeypatch, stage):
    monkeypatch.setenv('APP_FCM_ENABLED', 'false')
    item = {'handling_status': 'unacknowledged', 'notification_status': 'pending', **projected(), 'contact_stage': stage}
    with pytest.raises(PushError, match='FCM_NOT_ENABLED'):
        asyncio.run(publish_alert(item, idempotency_key=item['id']))


def test_no_stage_defaults_to_primary_and_invalid_stage_never_sends(tmp_path):
    repo, transport = ContactRepo(), OfflineTransport()
    publisher = AlertPublisher(SafetyRecipients(repo), transport, AcceptanceLedger(tmp_path / 'ledger.db'))
    item = {'handling_status': 'unacknowledged', 'notification_status': 'pending', **projected()}
    asyncio.run(publisher.publish(item, idempotency_key=item['id']))
    assert transport.calls == [('primary', 'primary')]
    with pytest.raises(PushError, match='ALERT_NOT_ELIGIBLE'):
        push(publisher, item, 'all')


def test_fcm_payload_names_stage_without_exposing_contacts_or_changing_alert_id():
    requests = []
    class Credentials:
        project = 'synthetic-project'
        async def access_token(self):
            return 'synthetic-oauth'
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'name': 'projects/synthetic-project/messages/offline'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            recipient = SafetyRecipients(ContactRepo()).snapshot('backup')[1][0]
            await FcmTransport(client, Credentials(), scope='synthetic|default').send(recipient, projected(contact_stage='backup'))
    asyncio.run(run())
    data = json.loads(requests[0].content)['message']['data']
    assert data['contact_stage'] == 'backup'
    assert data['alert_id'] == data['event_id'] == projected()['id']
    assert 'token' not in data and 'contact_id' not in data


def test_gateway_custom_async_handler_keeps_idempotency_contract_and_stages(settings, safety, clock):
    calls = []
    async def custom(item, *, idempotency_key):
        calls.append((item['id'], idempotency_key, item['contact_stage']))
        return {'accepted': True, 'is_mock': False}
    app = create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(), safety_repo=safety, push_handler=custom)
    with TestClient(app, base_url='http://localhost'):
        live(safety, key='staged-custom-handler')
        center = app.state.alert_center
        item = eventually(lambda: center.listing()['alerts'])[0]
        eventually(lambda: len(calls) == 1)
        eventually(lambda: stages(center, item['id'])['primary_status'] == 'accepted_by_provider')
        assert calls[0] == (item['id'], item['id'], 'primary')
        clock.now += 119
        assert center.claim(preferences=safety.get_preferences()) is None
        clock.now += 1
        eventually(lambda: len(calls) == 2)
        eventually(lambda: stages(center, item['id'])['backup_status'] == 'accepted_by_provider')
        assert calls[1] == (item['id'], item['id'], 'backup')
        center.handle(item['id'], 'ack')
        assert len(calls) == 2


def test_gateway_does_not_call_custom_handler_when_notifications_disabled(settings, safety):
    from safety_backend.schemas import Preferences
    safety.save_preferences(Preferences(notifications_enabled=False))
    calls = []
    async def custom(item, *, idempotency_key):
        calls.append(item)
        return {'accepted': True, 'is_mock': False}
    app = create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(), safety_repo=safety, push_handler=custom)
    with TestClient(app, base_url='http://localhost'):
        live(safety, key='notifications-disabled')
        center = app.state.alert_center
        eventually(lambda: center.listing()['alerts'])
        assert center.claim(preferences=safety.get_preferences()) is None
        assert calls == []


def test_primary_crash_lease_is_recovered_without_early_backup(tmp_path, clock):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    assert center.claim(preferences=preferences())['contact_stage'] == 'primary'
    restarted = AlertCenter(tmp_path)
    clock.now += 29.999
    assert restarted.claim(preferences=preferences()) is None
    clock.now += .001
    recovered = restarted.claim(preferences=preferences())
    assert recovered['contact_stage'] == 'primary'
    done(restarted, recovered)
    clock.now = 1800000119.999
    assert restarted.claim(preferences=preferences()) is None
    assert stages(restarted, item['id'])['backup_status'] == 'pending'
    clock.now = 1800000120
    assert restarted.claim(preferences=preferences())['contact_stage'] == 'backup'


def test_independent_center_instances_cannot_claim_same_stage(tmp_path, clock):
    from concurrent.futures import ThreadPoolExecutor
    one, two = AlertCenter(tmp_path), AlertCenter(tmp_path)
    one.upsert(projected())
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda center: center.claim(preferences=preferences()), [one, two]))
    assert len([job for job in jobs if job]) == 1
    assert next(job for job in jobs if job)['contact_stage'] == 'primary'


@pytest.mark.parametrize('change', [{'severity': 'warning'}, {'event_type': 'uncertain'}, {'review_status': 'withdrawn'}])
def test_downgrade_cancels_pending_backup_without_reenqueuing_on_later_update(tmp_path, clock, change):
    center = AlertCenter(tmp_path)
    item = center.upsert(projected())
    done(center, center.claim(preferences=preferences()))
    before = stages(center, item['id'])
    center.upsert(projected(**change))
    assert stages(center, item['id'])['backup_status'] == 'cancelled'
    center.upsert(projected(title='important again'))
    assert stages(center, item['id'])['backup_due_at'] == before['backup_due_at']
    clock.now += 1000
    assert AlertCenter(tmp_path).claim(preferences=preferences()) is None


def test_old_acceptance_receipts_remain_valid_for_staged_dispatch(tmp_path):
    repo, transport = ContactRepo(), OfflineTransport()
    recipient = SafetyRecipients(repo).snapshot()[1][0]
    ledger = AcceptanceLedger(tmp_path / 'ledger.db')
    item = {'handling_status': 'unacknowledged', 'notification_status': 'pending', **projected()}
    ledger.record(item['id'], recipient.key)
    publisher = AlertPublisher(SafetyRecipients(repo), transport, ledger)
    push(publisher, item)
    assert transport.calls == []
    with ledger.connect() as c:
        assert c.execute('SELECT 1 FROM acceptance WHERE alert_id=? AND recipient=?',
                         (item['id'] + ':primary', recipient.key)).fetchone()
    push(publisher, item, 'backup')
    assert transport.calls == [('backup', 'backup')]


def test_expired_backup_after_restart_does_not_block_primary_retry(tmp_path, clock):
    center = AlertCenter(tmp_path)
    center.upsert(projected())
    done(center, center.claim(preferences=preferences()), failed=True)
    clock.now += 121
    restarted = AlertCenter(tmp_path)
    primary = restarted.claim(preferences=preferences())
    assert primary['contact_stage'] == 'primary'
    done(restarted, primary, failed=True)
    backup = restarted.claim(preferences=preferences())
    assert backup['contact_stage'] == 'backup'
    done(restarted, backup, failed=True)
    assert restarted.claim(preferences=preferences()) is None
    clock.now += 2
    assert restarted.claim(preferences=preferences())['contact_stage'] == 'backup'

"""A outbox + C plugin contract; synthetic sources and provider only."""
from dataclasses import replace
from pathlib import Path
import sys

from fastapi.testclient import TestClient

from backend.phone_app import create_app
from backend.push_notifications.publisher import AcceptanceLedger, AlertPublisher, SafetyRecipients
from tests.conftest import MockVisionProvider
from tests.test_alert_center import safety, live, eventually, TOKEN


def test_a_outbox_calls_c_for_two_registered_phones_without_route_changes(settings, safety, tmp_path):
    from safety_backend.schemas import PushIn
    safety.register_token(PushIn(token='synthetic-phone-a', platform='android'))
    safety.register_token(PushIn(token='synthetic-phone-b', platform='android'))
    sent = []
    class OfflineProvider:
        async def send(self, recipient, alert):
            sent.append((recipient.token_id, alert['id']))
    publisher = AlertPublisher(SafetyRecipients(safety), OfflineProvider(), AcceptanceLedger(tmp_path / 'acceptance.sqlite3'))
    app = create_app(replace(settings, lan_token=TOKEN), MockVisionProvider(), safety_repo=safety, push_handler=publisher.publish)
    with TestClient(app, base_url='http://localhost') as client:
        live(safety, key='synthetic-new-after-start')
        center = app.state.alert_center
        item = eventually(lambda: center.listing()['alerts'])[0]
        eventually(lambda: center.get(item['id'])['notification_status'] == 'accepted_by_provider')
        assert len(sent) == 2 and len({row[0] for row in sent}) == 2
        assert {row[1] for row in sent} == {item['id']}
        headers = {'Authorization': 'Bearer ' + TOKEN}
        acknowledged = client.post('/api/alerts/' + item['id'] + '/ack', headers=headers)
        assert acknowledged.status_code == 200
        assert client.get('/api/alerts/' + item['id'], headers=headers).json()['handling_status'] == 'acknowledged'
        assert len(sent) == 2

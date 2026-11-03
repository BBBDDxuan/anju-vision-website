"""Synthetic preview alarms through real authenticated API, never a real phone."""
from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from ai_service.config import Config
from ai_service.preview_alerts import PreviewAlerts
from ai_service.types import Signal
from safety_backend.config import Settings
from safety_backend.main import create_app

TOKEN = "device-" + "x" * 32
USER = "user-" + "y" * 32


@pytest.fixture
def api(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "backend", user_token=USER,
        device_tokens={"camera_001": TOKEN}, worker_enabled=False, retry_seconds=0))
    with TestClient(app) as client:
        yield app, client


def connect(tmp_path, handler):
    return PreviewAlerts(Config(), tmp_path / "outbox", "camera_001", "http://local.test",
                         TOKEN, transport=httpx.MockTransport(handler), start=False)


def signal(kind="help_requested"):
    return Signal(kind, "person-1", .92, {"internal_test_field": "excluded"})


@pytest.mark.parametrize("kind", ["suspected_fall", "fire_detected", "help_requested"])
def test_candidate_reaches_api_notify_and_ack_without_media(tmp_path, api, kind):
    app, client = api
    requests = []
    def handle(request):
        requests.append(request)
        return client.request(request.method, request.url.path, content=request.content,
                              headers=dict(request.headers))
    relay = connect(tmp_path, handle)
    try:
        relay.mark_frame()
        event_id = relay.alarm(signal(kind), datetime.now(timezone.utc))
        assert relay.status()["pending"] == 1
        relay.delivery.tick()
        assert relay.status()["pending"] == 0
        event = app.state.repo.get_event(event_id)
        assert event["event_type"] == kind
        assert event["notification_status"] == "pending"
        assert event["evidence_id"] is None and not event["details"]["cloud_images"]
        assert "internal_test_field" not in event["details"]
        assert app.state.worker.step(("notify",))  # explicitly mock provider
        assert app.state.repo.get_event(event_id)["notification_status"] == "sent"
        headers = {"Authorization": "Bearer " + USER}
        assert client.post(f"/api/v1/events/{event_id}/ack", headers=headers).status_code == 200
        assert app.state.repo.get_event(event_id)["acknowledgement_status"] == "acknowledged"
        assert {r.url.path for r in requests} == {
            "/api/v1/devices/register", "/api/v1/devices/camera_001/heartbeat", "/api/v1/events"}
        assert TOKEN not in (tmp_path / "outbox/spool.sqlite3").read_bytes().decode("latin1")
        assert not list((tmp_path / "outbox").glob("*.jpg"))
    finally:
        relay.close()


def test_offline_queue_survives_restart_and_lost_reply_is_idempotent(tmp_path, api):
    app, client = api
    def offline(request):
        raise httpx.ConnectError("secret transport text")
    relay = connect(tmp_path, offline)
    event_id = relay.alarm(signal(), datetime.now(timezone.utc))
    relay.delivery.tick()
    assert relay.spool.pending_count() == 1
    relay.close()
    calls = []
    def recover(request):
        response = client.request(request.method, request.url.path,
                                  content=request.content, headers=dict(request.headers))
        if request.url.path == "/api/v1/events":
            calls.append(json.loads(request.content)["event_id"])
            if len(calls) == 1:
                raise httpx.ReadTimeout("secret response was lost")
        return response
    relay = connect(tmp_path, recover)
    try:
        relay.delivery.clock = lambda: 1000
        relay.delivery.tick()
        assert relay.spool.pending_count() == 1
        relay.delivery.clock = lambda: 2000
        relay.delivery.tick()
        assert relay.spool.pending_count() == 0
        assert calls == [event_id, event_id]
        assert len(client.get("/api/v1/events", headers={"Authorization": "Bearer " + USER}).json()) == 1
        jobs = app.state.repo.records("jobs")
        assert sum(row["kind"] == "notify" for row in jobs) == 1
        assert "secret" not in str(relay.spool.events())
    finally:
        relay.close()


def test_empty_preview_does_not_register_or_heartbeat(tmp_path):
    requests = []
    relay = connect(tmp_path, lambda request: requests.append(request))
    try:
        relay.delivery.tick()
        assert requests == []
        assert not relay.health.alive()
        relay.mark_frame()
        assert relay.health.alive()
        relay.disconnected()
        assert not relay.health.alive()
    finally:
        relay.close()


def test_unconfigured_credentials_do_not_create_outbox(tmp_path):
    with pytest.raises(ValueError, match="credentials not configured"):
        PreviewAlerts(Config(), tmp_path / "outbox", "camera_001", "http://local.test", "REPLACE_TOKEN")
    assert not (tmp_path / "outbox").exists()

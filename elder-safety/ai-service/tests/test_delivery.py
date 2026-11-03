from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from ai_service.config import Config
from ai_service.network import BackendClient, DeliveryError, DeliveryWorker, backend_url
from ai_service.spool import Spool, SpoolFull


TOKEN = "fake-test-device-secret"


def payload(event_id="stable-event-1"):
    return {"event_id": event_id, "device_id": "test-device", "event_type": "suspected_fall",
            "severity": "critical", "confidence": 0.9,
            "occurred_at": datetime.now(timezone.utc).isoformat(), "track_id": "person-1",
            "details": {"simulated": True}}


@pytest.fixture
def spool(tmp_path):
    instance = Spool(tmp_path / "state", "test-device", "http://local.test", 50_000_000)
    yield instance
    instance.close()


def jpeg(root):
    path = root / "key.jpg"
    ok, encoded = cv2.imencode(".jpg", np.zeros((64, 64, 3), np.uint8))
    assert ok
    path.write_bytes(encoded.tobytes())
    return path


def make_client(handler, config=None):
    return BackendClient("http://local.test", "test-device", TOKEN, config or Config(),
                         transport=httpx.MockTransport(handler))


def test_contract_registration_heartbeat_jpeg_and_event(spool):
    calls = []
    def handler(request):
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        calls.append(request)
        path = request.url.path
        if path == "/api/v1/devices/register":
            assert json.loads(request.content) == {"device_id": "test-device", "name": "Safety camera test-device"}
        elif path == "/api/v1/devices/test-device/heartbeat":
            assert json.loads(request.content) == {}
        elif path == "/api/v1/evidence":
            assert "multipart/form-data" in request.headers["content-type"]
            assert b'name="file"' in request.content and b'name="device_id"' in request.content
            assert b'test-device' in request.content and b'image/jpeg' in request.content
            return httpx.Response(200, json={"evidence_id": "evidence-jpg-1"})
        elif path == "/api/v1/events":
            body = json.loads(request.content)
            assert body["event_id"] == "stable-event-1"
            assert body["evidence_id"] == "evidence-jpg-1"
            assert body["event_type"] == "suspected_fall" and body["severity"] == "critical"
            assert 0 <= body["confidence"] <= 1 and datetime.fromisoformat(body["occurred_at"]).tzinfo
            return httpx.Response(201, json={"event_id": body["event_id"]})
        else:
            pytest.fail(f"unexpected endpoint {path}")
        return httpx.Response(200, json={})
    client = make_client(handler)
    spool.add_event(payload(), jpeg(spool.root))
    worker = DeliveryWorker(spool, client, Config(), clock=lambda: 100)
    worker.tick()
    assert spool.pending_count() == 0
    assert spool.event("stable-event-1")["delivered"] == 1
    assert [r.url.path for r in calls] == ["/api/v1/devices/register", "/api/v1/devices/test-device/heartbeat", "/api/v1/evidence", "/api/v1/events"]
    # Secret is never written into ledger.
    assert TOKEN.encode() not in (spool.root / "spool.sqlite3").read_bytes()
    client.close()


def test_timeout_after_server_acceptance_retries_same_id_and_exact_body(spool):
    bodies = []
    now = [100.0]
    def handler(request):
        if request.url.path == "/api/v1/events/stable-event-1/evidence":
            assert json.loads(request.content) == {"clip_evidence_id": "late-clip"}
            return httpx.Response(200, json={"event_id": "stable-event-1", "clip_evidence_id": "late-clip"})
        if request.url.path == "/api/v1/events":
            bodies.append(request.content)
            if len(bodies) == 1:
                raise httpx.ReadTimeout("credential-must-not-appear", request=request)
            return httpx.Response(409, json={"event_id": "stable-event-1"})
        return httpx.Response(200, json={})
    client = make_client(handler)
    spool.add_event(payload(), None)
    worker = DeliveryWorker(spool, client, Config(), clock=lambda: now[0])
    worker.tick()
    assert spool.pending_count() == 1
    assert spool.due(100) is None
    # A late clip cannot mutate the body of an ambiguous first send.
    spool.save_evidence("stable-event-1", "clip_evidence_id", "late-clip")
    now[0] += 1
    worker.tick()
    assert bodies[0] == bodies[1]
    worker.tick()  # The late clip is now also linked through the append API.
    assert spool.pending_count() == 0
    client.close()


def test_jpeg_failure_does_not_suppress_alarm_and_is_retried(spool):
    uploads = [0]
    alarms = []
    def handler(request):
        if request.url.path == "/api/v1/evidence":
            uploads[0] += 1
            if uploads[0] == 1:
                return httpx.Response(503, text=TOKEN)
            return httpx.Response(200, json={"evidence_id": "late-jpg"})
        if request.url.path == "/api/v1/events/stable-event-1/evidence":
            return httpx.Response(200, json={"event_id": "stable-event-1", **json.loads(request.content)})
        if request.url.path == "/api/v1/events":
            body = json.loads(request.content)
            alarms.append(body)
            return httpx.Response(200, json={"event_id": body["event_id"]})
        return httpx.Response(200, json={})
    spool.add_event(payload(), jpeg(spool.root))
    client = make_client(handler)
    worker = DeliveryWorker(spool, client, Config(), clock=lambda: 100)
    worker.tick()
    assert len(alarms) == 1 and "evidence_id" not in alarms[0]
    assert spool.pending_count() == 1
    worker.tick()
    assert spool.event("stable-event-1")["evidence_id"] == "late-jpg"
    assert spool.event("stable-event-1")["clip_status"] == "collecting"
    assert spool.pending_count() == 0
    client.close()


def test_registration_outage_keeps_events_and_recovers_after_restart(tmp_path):
    root = tmp_path / "durable"
    spool = Spool(root, "test-device", "http://local.test", 50_000_000)
    spool.add_event(payload(), None)
    failed = make_client(lambda request: httpx.Response(503, text=TOKEN))
    worker = DeliveryWorker(spool, failed, Config(), clock=lambda: 100)
    worker.tick()
    assert spool.pending_count() == 1 and not worker.registered
    failed.close()
    spool.close()
    reopened = Spool(root, "test-device", "http://local.test", 50_000_000)
    assert reopened.event("stable-event-1")["clip_status"] == "interrupted"
    success = make_client(lambda r: httpx.Response(200, json={"event_id": "stable-event-1"}))
    DeliveryWorker(reopened, success, Config(), clock=lambda: 101).tick()
    assert reopened.pending_count() == 0
    assert reopened.events()[0]["event_id"] == "stable-event-1"
    success.close()
    reopened.close()


def test_wrong_response_id_never_marks_delivered(spool):
    client = make_client(lambda r: httpx.Response(200, json={"event_id": "wrong"}))
    spool.add_event(payload(), None)
    DeliveryWorker(spool, client, Config(), clock=lambda: 100).tick()
    assert spool.event("stable-event-1")["delivered"] == 0
    assert spool.due(101)["last_error"] == "event_id_mismatch"
    client.close()


def test_heartbeat_runs_without_events_and_retries(spool):
    heartbeats = []
    now = [100.0]
    def handler(request):
        if request.url.path.endswith("heartbeat"):
            heartbeats.append(now[0])
            return httpx.Response(503 if len(heartbeats) == 1 else 200)
        return httpx.Response(200)
    client = make_client(handler)
    worker = DeliveryWorker(spool, client, Config(), clock=lambda: now[0])
    worker.tick()
    now[0] += 14
    worker.tick()
    assert len(heartbeats) == 1
    now[0] += 1
    worker.tick()
    assert len(heartbeats) == 2
    client.close()


def test_late_clip_upload_associates_with_backend_without_second_event(spool):
    path = spool.root / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 64))
    assert writer.isOpened()
    for _ in range(3):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()
    event_posts = []
    def handler(request):
        if request.url.path == "/api/v1/events":
            event_posts.append(json.loads(request.content))
            return httpx.Response(200, json={"event_id": "stable-event-1"})
        if request.url.path == "/api/v1/events/stable-event-1/evidence":
            return httpx.Response(200, json={"event_id": "stable-event-1", **json.loads(request.content)})
        if request.url.path == "/api/v1/evidence":
            assert b"video/mp4" in request.content and b'name="device_id"' in request.content
            return httpx.Response(200, json={"evidence_id": "mp4-1"})
        return httpx.Response(200)
    spool.add_event(payload(), None)
    client = make_client(handler)
    worker = DeliveryWorker(spool, client, Config(), clock=lambda: 100)
    worker.tick()
    spool.attach_clip("stable-event-1", path, "complete")
    worker.tick()
    assert len(event_posts) == 1
    assert spool.event("stable-event-1")["clip_evidence_id"] == "mp4-1"
    assert spool.event("stable-event-1")["clip_status"] == "linked"
    client.close()


@pytest.mark.parametrize("name,content", [("bad.png", b"x"), ("bad.jpg", b"not-a-jpeg"), ("bad.mp4", b"not-an-mp4")])
def test_evidence_restrictions_no_network(spool, name, content):
    def handler(request):
        pytest.fail("invalid evidence must not reach network")
    client = make_client(handler)
    path = spool.root / name
    path.write_bytes(content)
    with pytest.raises(DeliveryError):
        client.upload(path)
    client.close()


def test_retry_backoff_and_safe_error(spool, caplog):
    def handler(request):
        raise httpx.ConnectError(f"rtsp://user:{TOKEN}@camera", request=request)
    client = make_client(handler)
    spool.add_event(payload(), None)
    worker = DeliveryWorker(spool, client, Config(), clock=lambda: 100)
    worker.registered = True
    worker.next_heartbeat = 1000
    worker.tick()
    assert TOKEN not in caplog.text and "rtsp://" not in caplog.text
    assert spool.due(101)["attempts"] == 1
    assert worker.backoff(1000) == Config().retry_max_seconds
    client.close()


def test_spool_bound_to_device_and_backend(tmp_path):
    spool = Spool(tmp_path, "one", "http://one", 50_000_000)
    spool.close()
    with pytest.raises(ValueError):
        Spool(tmp_path, "two", "http://one", 50_000_000)
    with pytest.raises(ValueError):
        Spool(tmp_path, "one", "http://two", 50_000_000)


def test_disk_budget_stops_without_deleting_events(spool):
    spool.add_event(payload(), None)
    spool.max_bytes = 1
    with pytest.raises(SpoolFull):
        spool.add_event(payload("another"), None)
    assert len(spool.events()) == 1


@pytest.mark.parametrize("url", ["ftp://host", "http://user:password@host", "http://host/?secret=x", "http://host/path", "http://host/#fragment"])
def test_reject_unsafe_backend(url):
    with pytest.raises(ValueError):
        backend_url(url)


def test_stale_capture_and_retry_only_do_not_send_camera_heartbeats(spool):
    from ai_service.sources import SourceHealth

    now = [100.0]
    health = SourceHealth(3.5, clock=lambda: now[0])
    requests = []
    client = make_client(lambda r: (requests.append(r.url.path) or httpx.Response(200)))
    worker = DeliveryWorker(spool, client, Config(), clock=lambda: now[0], heartbeat_allowed=health.alive)
    worker.tick()
    assert requests == []  # No actual frame, not even initial registration.
    health.mark_frame()
    worker.tick()
    assert requests.count("/api/v1/devices/test-device/heartbeat") == 1
    now[0] += 16
    worker.tick()
    assert requests.count("/api/v1/devices/test-device/heartbeat") == 1
    health.mark_frame()
    worker.tick()
    assert requests.count("/api/v1/devices/test-device/heartbeat") == 2
    worker = DeliveryWorker(spool, client, Config(), clock=lambda: now[0], heartbeat_allowed=lambda: False)
    worker.registered = True
    now[0] += 16
    worker.tick()
    assert requests.count("/api/v1/devices/test-device/heartbeat") == 2
    client.close()

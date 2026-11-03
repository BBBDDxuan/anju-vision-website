from datetime import datetime, timezone, timedelta
import io
from PIL import Image
from sqlalchemy import update
from safety_backend.repository import stamp, now


def event(**values):
    body = {"event_id": "evt_one", "device_id": "camera_001", "event_type": "suspected_fall", "severity": "critical", "confidence": 0.85, "occurred_at": datetime.now(timezone.utc).isoformat(), "track_id": "person1"}
    body.update(values)
    return body


def submit(client, auth, **values):
    response = client.post("/api/v1/events", headers=auth["device"], json=event(**values))
    assert response.status_code == 200, response.text
    return response.json()


def test_authentication_and_device_scope(client, auth):
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/events").status_code == 401
    assert client.get("/api/v1/events", headers=auth["device"]).status_code == 403
    assert client.post("/api/v1/devices/register", headers=auth["device"], json={"device_id": "camera_002", "name": "非法"}).status_code == 403
    assert client.post("/api/v1/events", headers=auth["user"], json=event()).status_code == 403


def test_mock_closed_loop_and_monotonic_state(registered, auth, app):
    body = submit(registered, auth)
    assert body["state"] == "detected"
    assert len(app.state.repo.records("jobs")) == 3
    assert app.state.worker.step()
    sent = registered.get("/api/v1/events/evt_one", headers=auth["user"]).json()
    assert sent["notification_status"] == "sent" and sent["state"] == "notified"
    assert app.state.worker.step(("review",))
    reviewed = registered.get("/api/v1/events/evt_one", headers=auth["user"]).json()
    assert reviewed["review_status"] == "completed"
    assert reviewed["review_summary"]["uncertainty"] == "high"
    assert registered.post("/api/v1/events/evt_one/ack", headers=auth["user"]).json()["state"] == "acknowledged"
    app.state.repo.patch_event("evt_one", state="notified")
    assert registered.get("/api/v1/events/evt_one", headers=auth["user"]).json()["state"] == "acknowledged"
    assert registered.post("/api/v1/events/evt_one/resolve", headers=auth["user"]).json()["state"] == "resolved"
    assert registered.post("/api/v1/events/evt_one/ack", headers=auth["user"]).json()["state"] == "resolved"


def test_idempotency_and_window_dedup(registered, auth, app):
    payload = event()
    first = registered.post("/api/v1/events", headers=auth["device"], json=payload).json()
    repeat = registered.post("/api/v1/events", headers=auth["device"], json=payload).json()
    assert repeat["deduplicated"] and repeat["event_id"] == first["event_id"]
    assert registered.post("/api/v1/events", headers=auth["device"], json=dict(payload, confidence=0.2)).status_code == 409
    window = registered.post("/api/v1/events", headers=auth["device"], json=dict(payload, event_id="evt_two")).json()
    assert window["deduplicated"] and window["event_id"] == first["event_id"]
    assert len(app.state.repo.records("jobs")) == 3
    distinct = submit(registered, auth, event_id="evt_person2", track_id="person2")
    assert distinct["event_id"] == "evt_person2"


def test_validation_and_history(registered, auth):
    assert registered.post("/api/v1/events", headers=auth["device"], json=event(occurred_at="2026-10-28T10:00:00")).status_code == 422
    assert registered.post("/api/v1/events", headers=auth["device"], json=event(confidence=1.1)).status_code == 422
    submit(registered, auth)
    assert len(registered.get("/api/v1/events?event_type=suspected_fall&state=detected", headers=auth["user"]).json()) == 1
    assert registered.get("/api/v1/events?limit=999", headers=auth["user"]).status_code == 422
    assert registered.get("/api/v1/events?after=2026-01-01T00:00:00", headers=auth["user"]).status_code == 422
    assert registered.get("/api/v1/events?event_type=unknown", headers=auth["user"]).status_code == 422


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(out, "JPEG")
    return out.getvalue()


def test_evidence_auth_ownership_expiry_and_delete(registered, auth, app):
    response = registered.post("/api/v1/evidence", headers=auth["device"], data={"device_id": "camera_001"}, files={"file": ("../../unsafe.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 200, response.text
    eid = response.json()["evidence_id"]
    assert registered.get("/api/v1/evidence/" + eid).status_code == 401
    assert registered.get("/api/v1/evidence/" + eid, headers=auth["user"]).status_code == 200
    registered.post("/api/v1/devices/register", headers=auth["other"], json={"device_id": "camera_002", "name": "另一设备"})
    assert registered.post("/api/v1/events", headers=auth["other"], json=event(device_id="camera_002", evidence_id=eid)).status_code == 409
    submit(registered, auth, evidence_id=eid)
    assert registered.delete("/api/v1/events/evt_one", headers=auth["user"]).status_code == 200
    assert registered.get("/api/v1/evidence/" + eid, headers=auth["user"]).status_code == 404
    assert not list((app.state.settings.data_dir / "evidence").iterdir())
    assert not app.state.repo.records("jobs")


def test_expired_evidence_and_invalid_image(registered, auth, app):
    invalid = registered.post("/api/v1/evidence", headers=auth["device"], data={"device_id": "camera_001"}, files={"file": ("bad.jpg", b"not an image", "image/jpeg")})
    assert invalid.status_code == 415
    response = registered.post("/api/v1/evidence", headers=auth["device"], data={"device_id": "camera_001"}, files={"file": ("a.jpg", image_bytes(), "image/jpeg")})
    eid = response.json()["evidence_id"]
    repo = app.state.repo
    with repo.engine.begin() as connection:
        connection.execute(update(repo.evidence).where(repo.evidence.c.evidence_id == eid).values(expires_at=stamp(now() - timedelta(seconds=1))))
    app.state.worker.maintenance()
    assert registered.get("/api/v1/evidence/" + eid, headers=auth["user"]).status_code == 410
    assert not list((repo.s.data_dir / "evidence").iterdir())


def test_preferences_tokens_and_schema(registered, auth):
    response = registered.post("/api/v1/push-tokens", headers=auth["user"], json={"token": "mock-phone", "platform": "mock", "contact_id": "primary"})
    assert response.status_code == 200
    token_id = response.json()["token_id"]
    again = registered.post("/api/v1/push-tokens", headers=auth["user"], json={"token": "mock-phone", "platform": "mock", "contact_id": "backup"}).json()
    assert again["token_id"] == token_id
    assert registered.delete("/api/v1/push-tokens/" + token_id, headers=auth["user"]).status_code == 200
    prefs = registered.get("/api/v1/preferences", headers=auth["user"]).json()
    prefs["escalation_seconds"] = 60
    assert registered.put("/api/v1/preferences", headers=auth["user"], json=prefs).json()["escalation_seconds"] == 60
    schema = registered.get("/openapi.json").json()
    assert "/api/v1/events/{event_id}/ack" in schema["paths"]

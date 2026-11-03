from datetime import timedelta
from sqlalchemy import update
from safety_backend.main import create_app
from safety_backend.repository import stamp, now
from safety_backend.providers import ProviderError
from test_api import submit


def due(repo, kind):
    with repo.engine.begin() as connection:
        connection.execute(update(repo.jobs).where(repo.jobs.c.kind == kind).values(due_at=stamp(now() - timedelta(seconds=1))))


def test_cloud_failure_cannot_block_alert(registered, auth, app):
    submit(registered, auth)
    def fail(body):
        raise ProviderError("qwen_http_503")
    app.state.worker.providers.review = fail
    assert app.state.worker.step()
    for _ in range(app.state.settings.max_attempts):
        assert app.state.worker.step(("review",))
    body = app.state.repo.get_event("evt_one")
    assert body["review_status"] == "failed"
    assert body["notification_status"] == "sent" and body["state"] == "notified"
    failures = [row for row in app.state.repo.records("audit") if row["kind"] == "review_failed"]
    assert len(failures) == app.state.settings.max_attempts


def test_unacknowledged_event_escalates_and_ack_stops(registered, auth, app):
    submit(registered, auth)
    assert app.state.worker.step()
    due(app.state.repo, "escalate")
    assert app.state.worker.step()
    receipts = [row for row in app.state.repo.records("audit") if row["kind"] == "escalate_done"]
    assert len(receipts) == 1 and receipts[0]["body"]["contact_id"] == "backup"
    submit(registered, auth, event_id="evt_two", track_id="person2")
    registered.post("/api/v1/events/evt_two/ack", headers=auth["user"])
    due(app.state.repo, "escalate")
    assert not app.state.worker.step()


def test_push_failure_is_persisted_and_does_not_cancel_escalation(registered, auth, app):
    submit(registered, auth)
    def fail(body, contact):
        raise RuntimeError("secret=should-never-be-stored")
    app.state.worker.providers.notify = fail
    for _ in range(app.state.settings.max_attempts):
        assert app.state.worker.step()
    assert app.state.repo.get_event("evt_one")["notification_status"] == "failed"
    jobs = app.state.repo.records("jobs")
    assert any(row["kind"] == "escalate" and row["status"] == "pending" for row in jobs)
    assert "should-never" not in str(jobs)
    assert any(row["last_error"] == "RuntimeError" for row in jobs)


def test_restart_resumes_durable_notification(registered, auth, app, settings):
    submit(registered, auth)
    restarted = create_app(settings)
    assert restarted.state.worker.step()
    assert restarted.state.repo.get_event("evt_one")["notification_status"] == "sent"
    restarted.state.repo.engine.dispose()


def test_offline_transition_once_and_recovery(registered, auth, app):
    repo = app.state.repo
    with repo.engine.begin() as connection:
        connection.execute(update(repo.devices).values(last_seen_at=stamp(now() - timedelta(seconds=120))))
    app.state.worker.maintenance()
    app.state.worker.maintenance()
    offline = repo.list_events(event_type="device_offline")
    assert len(offline) == 1
    assert not registered.get("/api/v1/devices", headers=auth["user"]).json()[0]["online"]
    assert registered.post("/api/v1/devices/camera_001/heartbeat", headers=auth["device"]).json()["online"]

import json
import threading
import httpx
from safety_backend.providers import Providers
from safety_backend.main import create_app
from fastapi.testclient import TestClient
from test_api import image_bytes


def create_with_transport(settings, transport):
    return create_app(settings, lambda s, r: Providers(s, r, transport=transport))


def ingest(app, device):
    client = TestClient(app)
    client.post("/api/v1/devices/register", headers=device, json={"device_id": "camera_001", "name": "客厅"})
    ev = client.post("/api/v1/evidence", headers=device, data={"device_id": "camera_001"}, files={"file": ("frame.jpg", image_bytes(), "image/jpeg")}).json()["evidence_id"]
    payload = {"event_id": "evt_qwen", "device_id": "camera_001", "event_type": "suspected_fall", "severity": "critical", "confidence": 0.8, "occurred_at": "2026-10-28T12:00:00+08:00", "evidence_id": ev}
    assert client.post("/api/v1/events", headers=device, json=payload).status_code == 200
    return client


def test_qwen_request_shape_and_structured_result(settings, auth):
    requests = []
    def reply(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path.endswith("/chat/completions")
        assert body["model"] == settings.qwen_model
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        review = {"event_type": "uncertain", "observations": ["人处于低位"], "summary": "单帧无法确定跌倒", "limitations": ["缺少时序"], "uncertainty": "high"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(review)}}]})
    settings.review_mode, settings.allow_cloud_images, settings.api_key = "qwen", True, "fake-test-only"
    app = create_with_transport(settings, httpx.MockTransport(reply))
    ingest(app, auth["device"])
    assert app.state.worker.step(("review",))
    result = app.state.repo.get_event("evt_qwen")
    assert result["review_status"] == "completed" and result["severity"] == "critical"
    assert result["state"] == "detected" and result["notification_status"] == "pending"
    assert len(requests) == 1
    app.state.repo.engine.dispose()


def test_qwen_timeout_and_invalid_json_preserve_local_alert(settings, auth):
    settings.review_mode, settings.allow_cloud_images, settings.api_key = "qwen", True, "fake-test-only"
    calls = []
    def reply(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("secret-must-not-be-recorded")
        return httpx.Response(200, json={"choices": [{"message": {"content": "not JSON"}}]})
    app = create_with_transport(settings, httpx.MockTransport(reply))
    ingest(app, auth["device"])
    for _ in range(settings.max_attempts):
        assert app.state.worker.step(("review",))
    app.state.worker.step()
    result = app.state.repo.get_event("evt_qwen")
    assert result["notification_status"] == "sent" and result["review_status"] == "failed"
    assert "secret-must-not" not in str(app.state.repo.records("jobs"))
    app.state.repo.engine.dispose()


def test_notification_lane_progresses_while_review_is_blocked(settings, auth):
    app = create_app(settings)
    ingest(app, auth["device"])
    started, finish = threading.Event(), threading.Event()
    def slow_review(body):
        started.set()
        assert finish.wait(timeout=5)
        return {"summary": "test only"}
    app.state.worker.providers.review = slow_review
    thread = threading.Thread(target=app.state.worker.step, args=(("review",),))
    thread.start()
    try:
        assert started.wait(timeout=2)
        assert app.state.worker.step(("notify",))
        assert app.state.repo.get_event("evt_qwen")["notification_status"] == "sent"
        assert thread.is_alive()
    finally:
        finish.set()
        thread.join(timeout=5)
        app.state.repo.engine.dispose()

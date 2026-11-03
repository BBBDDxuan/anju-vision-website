from datetime import datetime, timezone


def test_help_gesture_event_enters_normal_alert_and_review_queue(registered, auth, app):
    payload = {"event_id": "help_gesture_one", "device_id": "camera_001",
               "event_type": "help_requested", "severity": "critical", "confidence": 0.8,
               "occurred_at": datetime.now(timezone.utc).isoformat(),
               "details": {"gesture": "both_hands_raised_candidate", "simulated": True}}
    response = registered.post("/api/v1/events", headers=auth["device"], json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["event_type"] == "help_requested"
    assert len(app.state.repo.records("jobs")) == 3
    assert app.state.worker.step(("notify",))
    assert app.state.worker.step(("review",))
    result = registered.get("/api/v1/events/help_gesture_one", headers=auth["user"]).json()
    assert result["notification_status"] == "sent"
    assert result["review_status"] == "completed"
    assert result["event_type"] == "help_requested"
    # Mock review has no evidence and deliberately stays uncertain.
    assert result["review_summary"]["event_type"] == "uncertain"
    filtered = registered.get("/api/v1/events?event_type=help_requested", headers=auth["user"])
    assert [e["event_id"] for e in filtered.json()] == ["help_gesture_one"]

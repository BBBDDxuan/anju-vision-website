from test_api import submit, image_bytes


def upload(client, auth):
    response = client.post("/api/v1/evidence", headers=auth["device"], data={"device_id": "camera_001"}, files={"file": ("frame.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 200
    return response.json()["evidence_id"]


def test_late_evidence_is_attached_without_realert(registered, auth, app):
    submit(registered, auth)
    app.state.worker.step()
    eid = upload(registered, auth)
    url = "/api/v1/events/evt_one/evidence"
    attached = registered.post(url, headers=auth["device"], json={"evidence_id": eid})
    assert attached.status_code == 200, attached.text
    assert attached.json()["evidence_id"] == eid
    assert attached.json()["state"] == "notified"
    assert registered.post(url, headers=auth["device"], json={"evidence_id": eid}).status_code == 200
    assert len([j for j in app.state.repo.records("jobs") if j["kind"] == "notify"]) == 1
    assert registered.post(url, headers=auth["device"], json={"evidence_id": upload(registered, auth)}).status_code == 409
    assert registered.post(url, headers=auth["other"], json={"evidence_id": eid}).status_code == 403
    assert registered.post(url, headers=auth["user"], json={"evidence_id": eid}).status_code == 403
    assert registered.post(url, headers=auth["device"], json={}).status_code == 422


def test_late_keyframe_requeues_unavailable_review(registered, auth, app):
    submit(registered, auth)
    app.state.settings.review_mode = "qwen"
    app.state.worker.step(("review",))
    assert app.state.repo.get_event("evt_one")["review_status"] == "unavailable"
    eid = upload(registered, auth)
    result = registered.post("/api/v1/events/evt_one/evidence", headers=auth["device"], json={"evidence_id": eid})
    assert result.json()["review_status"] == "pending"
    assert len([j for j in app.state.repo.records("jobs") if j["kind"] == "review"]) == 2

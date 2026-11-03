import copy

import pytest
from fastapi.testclient import TestClient

from backend.answer import query_memory
from backend.errors import AppError
from backend.events import validate_memory
from backend.main import create_app
from tests.conftest import MockVisionProvider, wait_job


def test_upload_analyze_query_evidence_and_range(client, analyzed):
    vid, memory = analyzed
    assert memory["is_mock"] is True and memory["model_run"]["provider"] == "TEST_MOCK"
    assert len(memory["events"]) == 4
    assert [e["start_ms"] for e in memory["events"]] == sorted(e["start_ms"] for e in memory["events"])
    result = client.post(f"/api/videos/{vid}/query", json={"question": "我的钥匙现在在哪里？"}).json()
    assert result["status"] == "partial" and result["is_mock"] is True
    assert result["inferences"] and "无法确认当前位置" in result["answer"]
    assert len(result["evidence"]) >= 2
    for evidence in result["evidence"]:
        for fid in evidence["frame_ids"]:
            assert client.get(f"/media/frames/{vid}/{fid}").headers["content-type"].startswith("image/jpeg")
    response = client.get(f"/media/videos/{vid}", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206 and len(response.content) == 100


def test_last_seen_and_paraphrase(client, analyzed):
    vid, _ = analyzed
    for question in ["最后一次在哪里看见钥匙串？", "我的钥匙上次在何处？"]:
        result = client.post(f"/api/videos/{vid}/query", json={"question": question}).json()
        assert "放入黑色背包" in result["answer"] and result["evidence"]


def test_as_of_prevents_future_leakage(client, analyzed):
    vid, _ = analyzed
    result = client.post(f"/api/videos/{vid}/query", json={"question": "钥匙最后在哪？", "as_of_ms": 1000}).json()
    assert "桌子" in result["answer"] and "放入" not in result["answer"]
    assert not result["inferences"] and all(e["end_ms"] <= 1000 for e in result["evidence"])
    assert client.post(f"/api/videos/{vid}/query", json={"question": "钥匙在哪？", "as_of_ms": 99999}).status_code == 422


def test_unknown_object_never_fabricates(client, analyzed):
    vid, _ = analyzed
    result = client.post(f"/api/videos/{vid}/query", json={"question": "我的护照在哪里？"}).json()
    assert result["status"] == "unknown" and result["evidence"] == []


def test_take_out_clears_container_and_reordering_is_stable(analyzed):
    _, document = analyzed
    document = {k: v for k, v in document.items() if k not in {"job", "is_mock"}}
    job = {"status": "completed", "is_mock": True}
    entry = copy.deepcopy(document["events"][1])
    entry.update(id="test_take_out", action="take_out", start_ms=4000, end_ms=5000)
    document["events"].append(entry)
    first = query_memory(document, job, "钥匙在哪里？")
    assert first["inferences"] == [] and "取出" in first["answer"]
    document["events"].reverse()
    second = query_memory(document, job, "钥匙在哪里？")
    assert first == second


def test_occlusion_is_not_exit(analyzed):
    _, document = analyzed
    key = document["events"][1]["subject_id"]
    hidden = copy.deepcopy(document["events"][1])
    hidden.update(id="occlusion", action="occluded", start_ms=4000, end_ms=4000, target_id=None)
    document["events"].append(hidden)
    from backend.state import replay
    state = replay(document, document["video_duration_ms"])[key]
    assert state.visibility == "occluded" and state.visibility != "out_of_view"


def test_partial_analysis_exposes_gap(settings, sample_video):
    with TestClient(create_app(settings, MockVisionProvider(fail_second=True)), base_url="http://localhost") as client:
        vid = client.post("/api/videos", files={"file": ("sample.mp4", sample_video.read_bytes())}).json()["video_id"]
        job = wait_job(client, client.post(f"/api/videos/{vid}/analyze").json()["id"])
        assert job["status"] == "partial" and job["failed_windows"]
        result = client.post(f"/api/videos/{vid}/query", json={"question": "钥匙最后在哪？"}).json()
        assert "失败窗口" in result["answer"] and result["status"] == "partial"


def test_real_configuration_missing_fails_honestly(settings, sample_video):
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.get("/api/health").json()["analysis_ready"] is False
        vid = client.post("/api/videos", files={"file": ("sample.mp4", sample_video.read_bytes())}).json()["video_id"]
        response = client.post(f"/api/videos/{vid}/analyze")
        assert response.status_code == 503 and response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
        assert client.get(f"/api/videos/{vid}/events").status_code == 409


def test_invalid_upload_and_request(client):
    assert client.post("/api/videos", files={"file": ("fake.mp4", b"not a video")}).status_code == 400
    assert client.post("/api/videos/nonexistent/query", json={"question": ""}).status_code == 422
    assert client.post("/api/videos", files={"file": ("fake.mp4", b"x")}, headers={"Origin": "https://untrusted.example"}).status_code == 403


def test_restart_preserves_events_and_interrupts_active(settings, sample_video):
    with TestClient(create_app(settings, MockVisionProvider()), base_url="http://localhost") as client:
        vid = client.post("/api/videos", files={"file": ("sample.mp4", sample_video.read_bytes())}).json()["video_id"]
        job = wait_job(client, client.post(f"/api/videos/{vid}/analyze").json()["id"])
        client.app.state.db.save_job({"id": "interrupted-test", "video_id": vid, "status": "running"})
    with TestClient(create_app(settings, MockVisionProvider()), base_url="http://localhost") as client:
        assert client.get(f"/api/videos/{vid}/events").json()["job"]["id"] == job["id"]
        assert client.get("/api/jobs/interrupted-test").json()["status"] == "interrupted"


@pytest.mark.parametrize("fault", ["frame_time", "cross_video", "unknown_frame", "unknown_evidence", "cycle", "placeholder"])
def test_semantic_validation_rejects_forged_evidence(analyzed, fault):
    _, doc = analyzed
    doc = copy.deepcopy({k: v for k, v in doc.items() if k not in {"job", "is_mock"}})
    for f in doc["frames"]:
        f.pop("url", None)
    if fault == "frame_time": doc["frames"][0]["pts_ms"] = -1
    elif fault == "cross_video": doc["evidence"][0]["video_id"] = "another-video"
    elif fault == "unknown_frame": doc["evidence"][0]["frame_ids"] = ["forged"]
    elif fault == "unknown_evidence": doc["events"][0]["evidence_ids"] = ["forged"]
    elif fault == "placeholder":
        doc["fixture_kind"] = "model_analysis"
        doc["frames"][0]["kind"] = "manual_placeholder"
    else:
        cycle = copy.deepcopy(doc["events"][1]); cycle["id"] = "cycle"
        cycle["subject_id"], cycle["target_id"] = cycle["target_id"], cycle["subject_id"]
        doc["events"].append(cycle)
    with pytest.raises(AppError): validate_memory(doc)

"""All fixtures are synthetic and explicitly mocked; no paid API calls occur."""
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.events import Extraction
from backend.errors import AppError
from backend.main import create_app
from backend.providers.base import ProviderResult


class MockVisionProvider:
    name, model, is_mock = "TEST_MOCK", "scripted-test-fixture-not-ai", True

    def __init__(self, fail_second=False):
        self.fail_second = fail_second

    def ready(self):
        return None

    def extract(self, *, window, entities, **kwargs):
        # Handwritten test actions, never loaded by the production application.
        ids = {e["label"]: e["id"] for e in entities}
        if window["start_ms"] == 0:
            data = {"entities": [{"id": name, "label": label, "aliases": [alias], "appearance": label} for name, label, alias in
                                 [("keys", "钥匙", "钥匙串"), ("bag", "黑色背包", "背包"), ("table", "桌子", "桌面"), ("sofa", "沙发", "沙发上")]],
                    "events": [{"subject_id": "keys", "target_id": "table", "action": "place_on", "frame_ids": [window["frames"][0]["id"]]},
                               {"subject_id": "keys", "target_id": "bag", "action": "put_in", "frame_ids": [window["frames"][2]["id"], window["frames"][3]["id"]]}]}
        else:
            if self.fail_second:
                raise AppError("MODEL_OUTPUT_INVALID", "测试注入：第二窗口JSON异常。")
            data = {"entities": [], "events": [
                {"subject_id": ids["黑色背包"], "target_id": ids["沙发"], "action": "move", "frame_ids": [window["frames"][0]["id"], window["frames"][1]["id"]]},
                {"subject_id": ids["黑色背包"], "action": "explicit_exit", "frame_ids": [window["frames"][-2]["id"], window["frames"][-1]["id"]]}]}
        return ProviderResult(Extraction.model_validate(data), {"test_mock": True})


def wait_job(client, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.05)
    raise AssertionError("job did not finish")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("synthetic_video") / "synthetic-test-only.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    assert writer.isOpened()
    for i in range(120):
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        cv2.rectangle(frame, (10 + i, 90), (35 + i, 115), (100, 210, 80), -1)
        cv2.putText(frame, f"SYNTHETIC TEST ONLY {i}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, .4, (255, 255, 255), 1)
        writer.write(frame)
    writer.release()
    return path


@pytest.fixture
def settings(tmp_path):
    # Legacy scripted object fixtures assume 8s/6s windows. Enhanced sampling
    # has dedicated tests, rather than changing what these mock actions mean.
    return Settings(data_dir=tmp_path / "data", enhanced_safety_sampling=False)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, MockVisionProvider()), base_url="http://localhost") as instance:
        yield instance


@pytest.fixture
def analyzed(client, sample_video):
    response = client.post("/api/videos", files={"file": (sample_video.name, sample_video.read_bytes(), "video/mp4")})
    assert response.status_code == 201, response.text
    vid = response.json()["video_id"]
    response = client.post(f"/api/videos/{vid}/analyze")
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "completed", job
    return vid, client.get(f"/api/videos/{vid}/events").json()

from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from safety_backend.config import Settings
from safety_backend.main import create_app

USER = "user-" + "x" * 32
DEVICE = "device-" + "x" * 32
OTHER = "other-" + "x" * 32

@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, user_token=USER, device_tokens={"camera_001": DEVICE, "camera_002": OTHER}, worker_enabled=False, retry_seconds=0, review_interval_seconds=0)

@pytest.fixture
def app(settings):
    app = create_app(settings)
    yield app
    app.state.repo.engine.dispose()

@pytest.fixture
def client(app):
    with TestClient(app) as value:
        yield value

@pytest.fixture
def auth():
    return {"user": {"Authorization": "Bearer " + USER}, "device": {"Authorization": "Bearer " + DEVICE}, "other": {"Authorization": "Bearer " + OTHER}}

@pytest.fixture
def registered(client, auth):
    assert client.post("/api/v1/devices/register", headers=auth["device"], json={"device_id": "camera_001", "name": "客厅"}).status_code == 200
    return client

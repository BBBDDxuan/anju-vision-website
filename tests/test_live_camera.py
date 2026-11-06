"""Synthetic camera bridge tests: no real camera, GPU or paid model calls."""
import io
import json
import struct
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from backend.errors import AppError
from backend.live_camera import LiveCamera, camera_router, read_exact


def test_exact_read_rejects_truncated_pipe():
    assert read_exact(io.BytesIO(b"abc"),3) == b"abc"
    with pytest.raises(EOFError): read_exact(io.BytesIO(b"ab"),3)


def test_live_status_reads_current_face_identity_and_survives_failures(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.config import Settings
    from backend.face_recognition import FaceRecognition
    from tests.test_member_profiles import fake_face
    faces = FaceRecognition(Settings(data_dir=tmp_path / 'identities'))
    monkeypatch.setattr(faces, '_features', lambda data: [fake_face()])
    faces.enroll('web-member', '合成家人', True, b'synthetic')
    monkeypatch.setattr(faces, '_candidate_identity', lambda candidate: {
        'status': 'matched', 'member_id': 'web-member', 'name': '合成家人', 'similarity': .9})
    faces.associate_candidate({'id': 'candidate-web'})
    controller = LiveCamera(tmp_path)
    controller.safety = SimpleNamespace(faces=faces, ready=lambda: None, enabled=True, model='synthetic-review')
    event = {'candidate_id': 'candidate-web', 'event_type': 'suspected_fall',
             'review_status': 'confirmed', 'review_model': 'synthetic-review'}
    controller.info['events'] = [event, {**event, 'event_type': 'fire_detected'}, {**event, 'review_status': 'pending'}]
    try:
        result = controller.status()
        assert result['events'][0]['member_id'] == 'web-member'
        assert result['events'][0]['face_identity']['name'] == '合成家人'
        assert 'member_id' not in controller.info['events'][0]  # Never mutate the worker snapshot.
        assert all('face_identity' not in e for e in result['events'][1:])
        faces.delete('web-member')
        assert controller.status()['events'][0]['face_identity']['reason'] == 'member_deleted'
        def unavailable(ids):
            raise RuntimeError('synthetic failure')
        monkeypatch.setattr(faces, 'event_identity', unavailable)
        broken = controller.status()['events'][0]
        assert broken['face_identity']['reason'] == 'identity_unavailable'
        assert broken['review_status'] == 'confirmed'
    finally:
        controller.close()


def client_for(controller, host="127.0.0.1", cloud=False):
    app = FastAPI()
    @app.exception_handler(AppError)
    async def error(request, exc):
        return JSONResponse({"error":exc.public()},status_code=exc.status)
    app.include_router(camera_router(controller,cloud))
    return TestClient(app,client=(host,12345))


def test_camera_rejects_remote_clients_and_cloud(tmp_path):
    controller = LiveCamera(tmp_path)
    for client in [client_for(controller,host="192.168.137.2"),client_for(controller,cloud=True)]:
        for route in ("status","frame.jpg"):
            assert client.get("/api/camera/"+route).status_code == 403
        assert client.post("/api/camera/start").status_code == 403


def test_camera_rejects_cross_origin_local_requests(tmp_path):
    client = client_for(LiveCamera(tmp_path))
    for method,route in [("get","status"),("post","start"),("post","stop")]:
        result = getattr(client,method)("/api/camera/"+route,headers={"Origin":"https://untrusted.example"})
        assert result.status_code == 403


def test_frame_freshness_stopped_and_reconnecting(tmp_path):
    controller = LiveCamera(tmp_path)
    client = client_for(controller)
    assert client.get("/api/camera/status").json()["state"] == "stopped"
    assert client.get("/api/camera/frame.jpg").status_code == 503
    controller.info["state"] = "running"
    controller.frame,controller.received = b"fake-test-jpeg",time.monotonic()
    response = client.get("/api/camera/frame.jpg")
    assert response.content == b"fake-test-jpeg" and response.headers["cache-control"].startswith("no-store")
    controller.received = time.monotonic()-5
    assert client.get("/api/camera/frame.jpg").status_code == 503
    assert client.get("/api/camera/status").json()["state"] == "stale"
    controller.received = time.monotonic()
    controller.info["state"] = "reconnecting"
    assert client.get("/api/camera/frame.jpg").status_code == 503


def test_missing_runtime_does_not_launch_or_leak_paths(tmp_path):
    response = client_for(LiveCamera(tmp_path)).post("/api/camera/start")
    assert response.status_code == 503
    assert str(tmp_path) not in response.text


def test_reader_protocol_and_cleanup(tmp_path):
    controller = LiveCamera(tmp_path)
    info = {"state":"running","frames":3,"jpeg_size":3}
    header = json.dumps(info).encode()
    process = type("FakeProcess",(),{"stdout":io.BytesIO(struct.pack("!I",len(header))+header+b"jpg")})()
    controller.process = process
    controller._read(process)
    # EOF cannot leave stale camera images labelled live.
    assert controller.frame is None and controller.info["state"] == "error"
    assert "jpeg_size" not in controller.info


@pytest.mark.parametrize("config_prefix", ["", "\ufeff"])
def test_owned_worker_start_ping_and_stop(tmp_path,monkeypatch,config_prefix):
    from backend import live_camera
    project = tmp_path/"elder-safety"
    home = tmp_path/"中文GPU环境"
    home.mkdir();(home/"python.exe").touch()
    for name in (".venv-gpu/Scripts/python.exe","scripts/web-camera-worker.py",".env"):
        p=project/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
    (project/".venv-gpu/pyvenv.cfg").write_text(config_prefix+"home = "+str(home), encoding="utf-8")
    # A UTF-8 config may include non-ASCII paths even on a GBK-default host.
    original_read_text = live_camera.Path.read_text
    def read_config(path, *args, **kwargs):
        if path.name == "pyvenv.cfg":
            assert kwargs.get("encoding") == "utf-8-sig"
        return original_read_text(path, *args, **kwargs)
    monkeypatch.setattr(live_camera.Path, "read_text", read_config)
    heartbeat = threading.Event()
    class Input(io.BytesIO):
        def write(self, data):
            result = super().write(data)
            if data == b"ping\n":
                heartbeat.set()
            return result
    class Process:
        pid = 123
        def __init__(self,*args,**kwargs):
            assert kwargs["stderr"] == live_camera.subprocess.DEVNULL
            assert "RTSP_URL" not in kwargs["env"]
            assert kwargs["env"]["SAFETY_CAMERA_CONNECTION_FILE"] == str(tmp_path/"runtime/camera-connection.json")
            assert not any("rtsp://" in arg for arg in args[0])
            self.stdin,self.stdout = Input(),io.BytesIO()
            self.done = False
        def poll(self): return 0 if self.done else None
        def wait(self,timeout): self.done=True;return 0
    monkeypatch.setattr(live_camera.subprocess,"Popen",Process)
    monkeypatch.setenv("RTSP_URL","rtsp://sensitive-source")
    monkeypatch.setattr(LiveCamera,"_read",lambda *args:None)
    profile = tmp_path/"runtime/camera-connection.json"
    profile.parent.mkdir()
    profile.write_text('{"rtsp_url":"rtsp://synthetic-camera"}')
    controller = LiveCamera(tmp_path)
    assert controller.start()["state"] == "starting"
    owned = controller.process
    # Starting the worker alone must keep it alive; no HTTP/status polling.
    assert heartbeat.wait(timeout=1)
    controller.status()
    assert b"ping" in owned.stdin.getvalue()
    assert controller.stop()["state"] == "stopped"
    assert owned.done and controller.process is None
    assert controller.heartbeat_stop.is_set()


def test_worker_death_never_leaves_recording_marked_active(tmp_path):
    controller = LiveCamera(tmp_path)
    controller.info = {"state":"running", "recording":True,
        "recording_status":{"enabled":True,"state":"recording"}}
    process = type("FakeProcess",(),{"stdout":io.BytesIO()})()
    controller.process=process
    controller._read(process)
    assert controller.info["recording"] is False
    assert controller.info["recording_status"]["state"] == "error"


def test_stop_updates_recording_state_even_when_worker_already_exited(tmp_path):
    controller = LiveCamera(tmp_path)
    controller.info = {"state":"error", "recording":True,
        "recording_status":{"enabled":True,"state":"recording"}}
    result=controller.stop()
    assert result["recording"] is False
    assert result["recording_status"]["state"] == "stopped"

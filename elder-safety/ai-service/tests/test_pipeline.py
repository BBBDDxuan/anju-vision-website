from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import threading

import cv2
import httpx
import numpy as np
import pytest

from ai_service.cli import main
from ai_service.config import Config, ModelConfig
from ai_service.engine import DetectionEngine
from ai_service.evidence import EvidenceRecorder
from ai_service.models import LocalModels, ModelConfigurationError
from ai_service.network import BackendClient, DeliveryWorker
from ai_service.sources import SourceError, file_samples, mock_samples, rtsp_samples
from ai_service.spool import Spool


def test_ring_pre_and_post_mp4_and_alarm_before_post_frames(tmp_path):
    config = Config()
    config.evidence.max_width = 320
    config.evidence.max_height = 240
    spool = Spool(tmp_path, "test", "http://local.test", config.max_spool_bytes)
    recorder = EvidenceRecorder(spool, config.evidence)
    engine = DetectionEngine(config)
    saved = None
    trigger_t = None
    samples = list(mock_samples(threading.Event(), realtime=False))
    sent = []
    def handler(request):
        if request.url.path == "/api/v1/evidence":
            return httpx.Response(200, json={"evidence_id": "jpg"})
        if request.url.path == "/api/v1/events":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"event_id": sent[-1]["event_id"]})
        return httpx.Response(200)
    client = BackendClient("http://local.test", "test", "fake-token", config, transport=httpx.MockTransport(handler))
    worker = DeliveryWorker(spool, client, config)
    for sample in samples:
        recorder.push(sample)
        if saved is None:
            for signal in engine.process(sample):
                saved = recorder.alarm(signal, sample, "test", {})
                trigger_t = sample.t
                # No post frames yet; alert delivery is already possible.
                assert not list((tmp_path / "evidence").glob("*.mp4"))
                assert recorder.active[0].frames[0][0] <= sample.t-1.8
                worker.tick()
                assert len(sent) == 1 and spool.event(saved)["delivered"] == 1
        elif sample.t >= trigger_t+config.evidence.post_seconds+0.2:
            break
    recorder.close()
    row = spool.event(saved)
    assert row["clip_status"] == "pending" and row["clip_note"] == "complete"
    image = cv2.imdecode(np.frombuffer(Path(row["keyframe"]).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape[:2] == (240, 320)
    capture = cv2.VideoCapture(row["clip_path"])
    try:
        assert capture.isOpened() and capture.read()[0]
        duration = capture.get(cv2.CAP_PROP_FRAME_COUNT)/capture.get(cv2.CAP_PROP_FPS)
        assert 3.8 <= duration <= 4.3
    finally:
        capture.release()
    assert json.loads(row["payload"])["details"]["simulated"]
    client.close()
    spool.close()


def test_eof_clip_marked_truncated(tmp_path):
    config = Config()
    config.evidence.max_width = 160
    config.evidence.max_height = 120
    spool = Spool(tmp_path, "d", "http://local.test", config.max_spool_bytes)
    recorder = EvidenceRecorder(spool, config.evidence)
    engine = DetectionEngine(config)
    for sample in mock_samples(threading.Event(), max_frames=35, realtime=False):
        recorder.push(sample)
        for signal in engine.process(sample):
            recorder.alarm(signal, sample, "d", {})
    recorder.close()
    assert len(spool.events()) == 1
    assert spool.events()[0]["clip_note"] == "truncated_by_disconnect_or_eof"
    spool.close()


def test_clip_encoder_failure_keeps_alarm(tmp_path, monkeypatch):
    config = Config()
    spool = Spool(tmp_path, "d", "http://local.test", config.max_spool_bytes)
    recorder = EvidenceRecorder(spool, config.evidence)
    engine = DetectionEngine(config)
    for sample in mock_samples(threading.Event(), max_frames=35, realtime=False):
        recorder.push(sample)
        for signal in engine.process(sample):
            recorder.alarm(signal, sample, "d", {})
    class BrokenWriter:
        def __init__(self, *args): pass
        def isOpened(self): return False
        def release(self): pass
    monkeypatch.setattr(cv2, "VideoWriter", BrokenWriter)
    recorder.close()
    assert spool.pending_count() == 1
    assert spool.events()[0]["clip_status"] == "unavailable"
    assert Path(spool.events()[0]["keyframe"]).is_file()
    spool.close()


def test_file_mode_reads_local_video_and_stops_at_eof(tmp_path):
    path = tmp_path / "input.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 64))
    assert writer.isOpened()
    for i in range(5):
        writer.write(np.full((64, 64, 3), i*30, np.uint8))
    writer.release()
    samples = list(file_samples(str(path), threading.Event(), realtime=False))
    assert len(samples) == 5
    assert [sample.t for sample in samples] == [0, 0.1, 0.2, 0.3, 0.4]
    assert all(not sample.simulated for sample in samples)
    assert len(list(file_samples(str(path), threading.Event(), max_frames=2, realtime=False))) == 2


def test_missing_video_file(tmp_path):
    with pytest.raises(SourceError):
        list(file_samples(str(tmp_path / "absent.mp4"), threading.Event()))


def test_rtsp_disconnect_backoff_reconnect_and_no_credentials(caplog):
    stop = threading.Event()
    config = Config()
    opens = [False, False, True, True]
    delays = []
    instances = []
    class FakeLink:
        def __init__(self, url, config):
            assert url == "rtsp://user:private-secret@camera/path"
            self.opened = opens.pop(0)
            self.reads = 0
            self.closed = False
            instances.append(self)
        def open(self, stop): return self.opened
        def read(self, stop):
            self.reads += 1
            return ("frame", np.zeros((10, 10, 3), np.uint8)) if self.reads == 1 else ("failed", None)
        def close(self): self.closed = True
    def wait(delay):
        delays.append(delay)
        return False
    samples = list(rtsp_samples("rtsp://user:private-secret@camera/path", config, stop,
                   max_frames=2, link_factory=FakeLink, clock=iter([1.0, 2.0]).__next__, waiter=wait))
    assert delays == [0.5, 1.0, 2.0]
    assert [sample.session for sample in samples] == [1, 2]
    assert all(instance.closed for instance in instances)
    assert "private-secret" not in caplog.text and "camera/path" not in caplog.text


def test_rtsp_stop_during_backoff():
    stop = threading.Event()
    closed = []
    class FakeLink:
        def __init__(self, *args): pass
        def open(self, stop): return False
        def close(self): closed.append(True)
    waits = []
    def wait(delay):
        waits.append(delay)
        stop.set()
        return True
    assert not list(rtsp_samples("rtsp://camera", Config(), stop, link_factory=FakeLink, waiter=wait))
    assert waits == [0.5] and closed == [True]


def test_models_disabled_without_optional_import():
    def factory(*args, **kwargs):
        pytest.fail("no weights means no model constructor/import/download")
    with pytest.raises(ModelConfigurationError, match="no local weights"):
        LocalModels(ModelConfig(), factory=factory)


@pytest.mark.parametrize("weights", ["yolo11n-pose.pt", "https://example.com/model.pt", "D:/missing-model.pt"])
def test_weights_must_exist_locally_at_absolute_path(weights):
    with pytest.raises(ModelConfigurationError):
        LocalModels(ModelConfig(pose_weights=weights))


def test_flame_license_and_class_validation(tmp_path):
    weight = tmp_path / "custom-flame.pt"
    weight.write_bytes(b"fake-test-placeholder-never-loaded")
    with pytest.raises(ModelConfigurationError):
        LocalModels(ModelConfig(flame_weights=str(weight)))
    metadata = {"name": "fake model", "source": "test fixture", "license": "test-only", "usage_review": "not production"}
    class Fake:
        task = "detect"
        names = {0: "person", 1: "car", 2: "chair", 3: "tv", 4: "dog", 5: "bicycle", 6: "flame"}
    with pytest.raises(ModelConfigurationError, match="COCO"):
        LocalModels(ModelConfig(flame_weights=str(weight), flame_classes=["flame"], flame_license=metadata), factory=lambda *a, **k: Fake())
    Fake.names = {0: "flame", 1: "smoke"}
    models = LocalModels(ModelConfig(flame_weights=str(weight), flame_classes=["flame"], flame_license=metadata), factory=lambda *a, **k: Fake())
    assert models.flame is not None
    with pytest.raises(ModelConfigurationError, match="absent"):
        LocalModels(ModelConfig(flame_weights=str(weight), flame_classes=["fire"], flame_license=metadata), factory=lambda *a, **k: Fake())


def test_wrong_pose_keypoint_schema_rejected(tmp_path):
    weight = tmp_path / "custom-pose.pt"
    weight.write_bytes(b"fake")
    class Fake:
        task = "pose"
        model = type("Model", (), {"yaml": {"kpt_shape": [21, 3]}})()
    with pytest.raises(ModelConfigurationError, match="17"):
        LocalModels(ModelConfig(pose_weights=str(weight), pose_license={"name": "test", "source": "fixture", "license": "test-only", "usage_review": "test"}), factory=lambda *a, **k: Fake())


@pytest.mark.parametrize("data", [
    {"fall": {"hold_seconds": -1}}, {"fire": {"confidence": 1.1}},
    {"fall": {"angle_degrees": 20}}, {"evidence": {"fps": 100}},
    {"unknown": 1}, {"fall": {"hold_seconds": "0.8"}},
    {"fire": {"min_frames": 2.5}}, {"evidence": {"post_seconds": 40}},
    {"evidence": {"max_active_clips": 10000}}, {"models": {"flame_classes": "flame"}},
])
def test_config_rejects_unsafe_or_misspelled_settings(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        Config.load(str(path))


def test_example_config_loads():
    assert Config.load(str(Path(__file__).resolve().parents[1] / "config.example.json")).evidence.fps == 10


def test_cli_mock_offline_no_network(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFETY_DEVICE_ID", "cli-mock")
    monkeypatch.setenv("SAFETY_DEVICE_TOKEN", "fake-offline-only")
    def forbidden(*args, **kwargs):
        pytest.fail("offline mock must not issue network requests")
    monkeypatch.setattr(httpx.Client, "post", forbidden)
    state = tmp_path / "cli-state"
    result = main(["--source", "mock", "--backend", "http://127.0.0.1:8010", "--offline", "--no-realtime", "--state-dir", str(state)])
    assert result == 0
    spool = Spool(state, "cli-mock", "http://127.0.0.1:8010", 512_000_000)
    assert len(spool.events()) == 2
    assert all(row["clip_path"] and row["clip_status"] == "pending" for row in spool.events())
    spool.close()


def test_cli_rejects_missing_device_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFETY_DEVICE_ID", "cli-test")
    monkeypatch.delenv("SAFETY_DEVICE_TOKEN", raising=False)
    assert main(["--source", "mock", "--backend", "http://127.0.0.1:8010", "--offline", "--state-dir", str(tmp_path)]) == 2
    assert not list(tmp_path.glob("*.sqlite3"))


def test_capture_parent_deadline_and_force_cleanup(monkeypatch):
    from ai_service.sources import CaptureLink
    import ai_service.sources as sources
    # Exercise the parent deadline without creating a process or RTSP request.
    ticks = iter([0.0, 0.0, 0.2, 0.4, 0.6])
    monkeypatch.setattr(sources.time, "monotonic", lambda: next(ticks))
    class Pipe:
        closed = False
        def poll(self, timeout): return False
        def close(self): self.closed = True
    class Process:
        terminated = False
        killed = False
        def is_alive(self): return not self.killed
        def terminate(self): self.terminated = True
        def kill(self): self.killed = True
        def join(self, timeout): pass
    link = object.__new__(CaptureLink)
    link.pipe, link.process = Pipe(), Process()
    assert link.receive(0.3, threading.Event()) == ("failed", None)
    link.close()
    assert link.pipe.closed and link.process.terminated and link.process.killed


def test_native_capture_failure_returns_no_url(monkeypatch):
    from ai_service.sources import _capture_child
    import ai_service.sources as sources
    monkeypatch.setattr(sources.os, "dup2", lambda *args: None)
    def broken_capture(*args):
        raise RuntimeError("rtsp://user:secret-camera-password@camera")
    monkeypatch.setattr(cv2, "VideoCapture", broken_capture)
    class Pipe:
        messages = []
        def send(self, message): self.messages.append(message)
        def close(self): pass
    pipe = Pipe()
    _capture_child(pipe, "rtsp://user:secret-camera-password@camera", 100, 100)
    assert pipe.messages == [("failed", None)]


def test_cli_rtsp_url_cannot_leak_in_error(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SAFETY_DEVICE_ID", "test")
    monkeypatch.setenv("SAFETY_DEVICE_TOKEN", "device-secret")
    assert main(["--source", "rtsp://user:camera-secret@camera", "--backend", "http://127.0.0.1:8010",
                 "--offline", "--state-dir", str(tmp_path)]) == 2
    assert "camera-secret" not in caplog.text and "device-secret" not in caplog.text

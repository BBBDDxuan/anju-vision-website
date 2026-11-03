"""Statistics-only camera probe tests; synthetic injected frames, no network."""
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from datetime import datetime, timezone

import numpy as np
import pytest


@pytest.fixture
def probe():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("bounded_camera_probe_test", root / "scripts/probe-camera-ai.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments(tmp_path, seconds="1"):
    return ["--config", str(tmp_path / "unused.json"), "--expected-host", "camera.test",
            "--device", "cpu", "--seconds", seconds, "--max-frames", "10",
            "--report", str(tmp_path / "result.json")]


@pytest.mark.parametrize("seconds", ["nan", "0", "61"])
def test_probe_rejects_invalid_duration(probe, tmp_path, seconds, capsys):
    assert probe.main(arguments(tmp_path, seconds)) == 2
    report = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert report["error"] == "invalid_bounds"
    assert not report["media_saved"] and not report["network_uploads"]


def test_probe_rejects_unexpected_camera_without_exposing_credentials(probe, tmp_path, monkeypatch, capsys):
    secret = "private-test-password"
    monkeypatch.setenv("RTSP_URL", f"rtsp://user:{secret}@wrong-camera.test/stream")
    assert probe.main(arguments(tmp_path)) == 2
    text = (tmp_path / "result.json").read_text(encoding="utf-8")
    assert json.loads(text)["error"] == "unexpected_rtsp_target"
    assert secret not in text and secret not in capsys.readouterr().out


def test_probe_success_and_cleanup_with_fake_models(probe, tmp_path, monkeypatch, capsys):
    from ai_service.config import Config
    from ai_service.sources import CapturedFrame
    import ai_service.models as models
    import ai_service.sources as sources
    import ai_service.runtime_env as runtime_env
    def model():
        return SimpleNamespace(model=SimpleNamespace(parameters=lambda: iter([SimpleNamespace(device="cpu")])))
    class Models:
        def __init__(self, config): self.pose, self.flame = model(), model()
        def infer(self, image): return [], []
    class Link:
        closed = False
        def __init__(self, *args): self.sequence = 0
        def open(self, stop): return True
        def read(self, stop):
            self.sequence += 1
            return "frame", CapturedFrame(np.zeros((8, 12, 3), np.uint8),
                    time.perf_counter(), datetime.now(timezone.utc), self.sequence)
        def close(self): Link.closed = True
    monkeypatch.setenv("RTSP_URL", "rtsp://test:private-test-password@camera.test/stream")
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, path: Config()))
    monkeypatch.setattr(models, "LocalModels", Models)
    monkeypatch.setattr(sources, "CaptureLink", Link)
    monkeypatch.setattr(runtime_env, "configure_model_runtime", lambda: tmp_path)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    assert probe.main(arguments(tmp_path)) == 0
    text = (tmp_path / "result.json").read_text(encoding="utf-8")
    report = json.loads(text)
    assert report["frames"] == 10 and report["status"] == "passed"
    assert report["width"] == 12 and report["height"] == 8 and Link.closed
    assert not report["media_saved"] and not report["network_uploads"]
    assert not report["actual_camera_exposure_latency_measured"]
    assert "private-test-password" not in text
    assert "private-test-password" not in capsys.readouterr().out

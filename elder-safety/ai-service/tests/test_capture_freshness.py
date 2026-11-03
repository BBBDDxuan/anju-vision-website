"""Latest-frame capture regression tests; no network or camera access."""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import threading

import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.sources import CapturedFrame, _LatestCapture, rtsp_samples


def frame(sequence, t):
    return CapturedFrame(np.zeros((8, 12, 3), np.uint8), t,
                         datetime.fromtimestamp(t, timezone.utc), sequence)


def samples_from(frames):
    class Link:
        closed = False
        def __init__(self, *args): self.frames = iter(frames)
        def open(self, stop): return True
        def read(self, stop): return "frame", next(self.frames)
        def close(self): Link.closed = True
    samples = list(rtsp_samples("rtsp://camera/verified-path", Config(),
                   threading.Event(), max_frames=len(frames), link_factory=Link))
    assert Link.closed
    return samples


def test_skipped_decode_sequences_keep_temporal_continuity():
    samples = samples_from([frame(1, 10.0), frame(4, 10.1), frame(7, 10.2)])
    assert [s.session for s in samples] == [1, 1, 1]
    assert [s.occurred_at.timestamp() for s in samples] == [10.0, 10.1, 10.2]
    engine = DetectionEngine(Config())
    for sample in samples:
        assert not engine.discontinuous(sample)
        engine.process(sample)


def test_actual_time_gap_still_resets_detection():
    samples = samples_from([frame(1, 10.0), frame(15, 11.0)])
    engine = DetectionEngine(Config())
    engine.process(samples[0])
    assert engine.discontinuous(samples[1])


@pytest.mark.parametrize("sequence,t", [(2, 10.1), (4, 10.0)])
def test_duplicate_or_regressing_capture_reconnects(sequence, t):
    stop = threading.Event()
    class Link:
        def __init__(self, *args): self.frames = iter([frame(2, 10.0), frame(sequence, t)])
        def open(self, stop): return True
        def read(self, stop): return "frame", next(self.frames)
        def close(self): pass
    def wait(delay):
        stop.set()
        return True
    result = list(rtsp_samples("rtsp://camera/verified-path", Config(), stop,
                              link_factory=Link, waiter=wait))
    assert len(result) == 1


def reader_with(frame_value, failed=False):
    reader = object.__new__(_LatestCapture)
    reader.condition = threading.Condition()
    reader.latest, reader.failed, reader.stopping = frame_value, failed, False
    return reader


def test_stale_pending_frame_is_not_returned(monkeypatch):
    monkeypatch.setattr("ai_service.sources.time.perf_counter", lambda: 15.0)
    reader = reader_with(frame(1, 10.0))
    assert reader.read(1) == ("failed", None)
    assert reader.latest is None


def test_failed_capture_never_drains_pending_frame():
    assert reader_with(frame(1, 10.0), failed=True).read(1) == ("failed", None)


def test_latest_capture_overwrites_pending_frame_and_releases():
    class Capture:
        def __init__(self):
            self.count = 0
            self.blocked, self.finish, self.released = (threading.Event() for _ in range(3))
        def read(self):
            self.count += 1
            if self.count <= 3:
                return True, np.full((8, 12, 3), self.count, np.uint8)
            self.blocked.set()
            self.finish.wait(3)
            return False, None
        def release(self): self.released.set()
    capture = Capture()
    reader = _LatestCapture(capture)
    try:
        assert capture.blocked.wait(2)
        kind, latest = reader.read(2)
        assert kind == "frame" and latest.sequence == 3
        assert np.all(latest.image == 3)
    finally:
        capture.finish.set()
        reader.close()
    assert capture.released.wait(2)
    assert reader.failed and reader.latest is None


def test_camera_probe_unwraps_captured_frame(monkeypatch, tmp_path):
    import ai_service.sources as sources
    class Link:
        closed = False
        def __init__(self, *args): pass
        def open(self, stop): return True
        def read(self, stop): return "frame", frame(1, 10.0)
        def close(self): Link.closed = True
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("camera_probe_test", root / "scripts/probe-camera.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sources, "CaptureLink", Link)
    monkeypatch.setenv("RTSP_URL", "rtsp://test:private-secret@camera/documented")
    target = tmp_path / "probe.json"
    monkeypatch.setattr(sys, "argv", ["probe", "--frames", "1", "--report", str(target)])
    assert module.main() == 0
    import json
    result = json.loads(target.read_text(encoding="utf-8"))
    assert result["width"] == 12 and result["height"] == 8
    assert result["decoded_frames"] == 1 and Link.closed
    assert "private-secret" not in target.read_text(encoding="utf-8")


def test_capture_uses_high_resolution_clock_when_coarse_clock_repeats(monkeypatch):
    from types import SimpleNamespace
    import ai_service.sources as sources
    reader = object.__new__(_LatestCapture)
    reader.condition = threading.Condition()
    reader.latest, reader.failed, reader.stopping = None, False, False
    timestamps = []
    class Capture:
        reads = 0
        released = False
        def read(self):
            if reader.latest is not None:
                timestamps.append(reader.latest.t)
            self.reads += 1
            return self.reads <= 2, np.zeros((8, 12, 3), np.uint8)
        def release(self): self.released = True
    reader.capture = Capture()
    fine = iter([20.001, 20.002])
    monkeypatch.setattr(sources, "time", SimpleNamespace(
        monotonic=lambda: 20.0, perf_counter=lambda: next(fine)))
    reader._run()
    assert timestamps == [20.001, 20.002]
    assert reader.failed and reader.capture.released

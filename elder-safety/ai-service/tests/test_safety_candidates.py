"""Local candidate/CLI tests: synthetic samples and mock inference, no weights."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

import cv2
import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
import ai_service.safety_candidates as module
from ai_service.safety_candidates import SafetyCandidateCollector
from ai_service.types import Flame, Sample, Signal


ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan-safety-video.py"


def sample(t, session=0, size=(48, 64), flames=()):
    return Sample(np.full((*size, 3), round(t * 10) % 256, np.uint8), t,
                  ORIGIN + timedelta(seconds=t), session=session, flames=list(flames))


def fire_signal(index=0, event_type="fire_detected", **kwargs):
    return Signal(event_type, f"track-{index}", kwargs.get("confidence", 0.9),
                  kwargs.get("details", {"algorithm": "synthetic"}))


def inject(collector, monkeypatch, callback):
    original = collector.engine.process

    def process(s):
        original(s)  # Retain actual engine clock/discontinuity behavior.
        return callback(s)

    monkeypatch.setattr(collector.engine, "process", process)


def assert_published(candidate, output):
    assert set(candidate) - {"subject_bbox_hint"} == {"id", "event_type", "track_id", "confidence", "details",
                              "t", "occurred_at", "frames"}
    if "subject_bbox_hint" in candidate:
        hint = candidate["subject_bbox_hint"]
        assert len(hint) == 4 and all(np.isfinite(hint))
        assert hint[0] < hint[2] and hint[1] < hint[3]
    uuid.UUID(candidate["id"].removeprefix("sc_"))
    directory = output.resolve() / candidate["id"]
    assert json.loads((directory / "manifest.json").read_text("utf-8")) == candidate
    datetime.fromisoformat(candidate["occurred_at"])
    assert 0 < len(candidate["frames"]) <= 12
    assert json.loads(json.dumps(candidate, allow_nan=False)) == candidate
    times = [f["pts_ms"] for f in candidate["frames"]]
    assert times == sorted(set(times))
    for frame in candidate["frames"]:
        assert set(frame) == {"id", "pts_ms", "path"}
        path = Path(frame["path"])
        assert path.is_absolute() and path.parent == directory
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        assert image is not None and max(image.shape[:2]) <= 960
    assert not list(directory.glob("*.partial"))


def test_same_engine_and_unchanged_config_every_frame(tmp_path, monkeypatch):
    config = Config()
    config.recording.enabled = True  # Collector must not create a recorder.
    before = asdict(config)
    collector = SafetyCandidateCollector(config, tmp_path)
    engine = collector.engine
    reference = DetectionEngine(config)
    original = engine.process
    calls, expected, actual = [], [], []

    def spy(s):
        calls.append(s)
        return original(s)

    monkeypatch.setattr(engine, "process", spy)
    for i in range(51):
        s = sample(i / 20, flames=[Flame((1, 1, 30, 30), 0.9)])
        expected.extend((s.t, signal) for signal in reference.process(s))
        actual.extend(collector.process(s))
    actual.extend(collector.finish())
    assert collector.engine is engine and len(calls) == 51
    assert len(expected) == len(actual) == 1
    assert actual[0]["t"] == expected[0][0]
    for name in ("event_type", "track_id", "confidence", "details"):
        assert actual[0][name] == getattr(expected[0][1], name)
    assert asdict(config) == before
    assert not list(tmp_path.glob("*.mp4"))
    assert_published(actual[0], tmp_path)


def test_two_second_pre_one_second_post_max_twelve(tmp_path, monkeypatch):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    inject(collector, monkeypatch, lambda s: [fire_signal()] if s.t == 2 else [])
    ready = []
    for i in range(121):
        ready.extend(collector.process(sample(i / 40)))
        if i < 120:
            assert ready == []
            assert not list(tmp_path.glob("*/manifest.json"))
    assert len(ready) == 1
    assert [f["pts_ms"] for f in ready[0]["frames"]] == list(range(0, 3000, 250))
    assert collector.finish() == []
    assert_published(ready[0], tmp_path)


def test_sparse_flush_never_duplicates_or_pads_and_detaches_images(tmp_path, monkeypatch):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    signal = fire_signal(details={"nested": [1]})
    inject(collector, monkeypatch, lambda s: [signal] if s.t == 0.1 else [])
    first = sample(0)
    collector.process(first)
    first.image[:] = 255
    collector.process(sample(0.1))
    signal.details["nested"].append(2)
    collector.process(sample(0.25))
    ready = collector.finish()
    assert len(ready) == 1
    assert [f["pts_ms"] for f in ready[0]["frames"]] == [0, 250]
    assert ready[0]["details"] == {"nested": [1]}
    first_path = Path(ready[0]["frames"][0]["path"])
    assert cv2.imdecode(np.frombuffer(first_path.read_bytes(), np.uint8), 1).max() == 0
    assert collector.finish() == []
    assert_published(ready[0], tmp_path)


@pytest.mark.parametrize("size,expected", [((1200, 600), (960, 480)),
                                          ((600, 1200), (480, 960)),
                                          ((48, 64), (48, 64))])
def test_jpeg_longest_edge_and_no_upscale(tmp_path, monkeypatch, size, expected):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    inject(collector, monkeypatch, lambda s: [fire_signal()])
    collector.process(sample(0, size=size))
    result = collector.finish()[0]
    image = cv2.imdecode(np.frombuffer(Path(result["frames"][0]["path"]).read_bytes(), np.uint8), 1)
    assert image.shape[:2] == expected


def test_only_supported_signals_and_eight_pending_limit(tmp_path, monkeypatch):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    signals = [fire_signal(event_type="other")]
    signals += [fire_signal(i, event_type=name) for i, name in enumerate(
        ["suspected_fall", "help_requested", "fire_detected"] * 4)]
    inject(collector, monkeypatch, lambda s: signals)
    collector.process(sample(0))
    collector.process(sample(0.25))
    assert len(collector._pending) == 8
    assert all(len(p.frames) <= 12 for p in collector._pending)
    ready = collector.finish()
    assert len(ready) == 8
    assert {c["event_type"] for c in ready} == module.EVENT_TYPES
    assert len({c["id"] for c in ready}) == 8
    for candidate in ready:
        assert_published(candidate, tmp_path)


def test_pending_slots_reused_after_completion(tmp_path, monkeypatch):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    inject(collector, monkeypatch, lambda s: [fire_signal(i) for i in range(10)])
    ready = []
    for i in range(5):
        ready.extend(collector.process(sample(i / 4)))
    assert len(ready) == 8 and len(collector._pending) == 8
    assert len(collector.finish()) == 8


def test_reset_discards_temporal_state_preserves_engine_and_no_cross_session(tmp_path, monkeypatch):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    engine = collector.engine
    inject(collector, monkeypatch, lambda s: [fire_signal()] if s.t == 0 else [])
    collector.process(sample(0))
    collector.process(sample(0.25))
    collector.reset()
    assert collector.engine is engine
    assert engine.last_t is None and engine.session is None
    assert not engine.tracker.tracks and not engine.fire.states
    assert collector.finish() == []
    collector.process(sample(0, session=1))
    result = collector.finish()[0]
    assert [f["pts_ms"] for f in result["frames"]] == [0]
    assert len(list(tmp_path.glob("*/manifest.json"))) == 1


@pytest.mark.parametrize("next_t,next_session", [(0.5, 1), (0, 0), (2, 0)])
def test_automatic_session_rewind_and_gap_flush_without_bridging(tmp_path, monkeypatch, next_t, next_session):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    inject(collector, monkeypatch, lambda s: [fire_signal()])
    collector.process(sample(0))
    collector.process(sample(0.25))
    ready = collector.process(sample(next_t, session=next_session))
    assert len(ready) == 2
    assert all([f["pts_ms"] for f in c["frames"]] == [0, 250] for c in ready)
    new = collector.finish()
    assert len(new) == 1
    assert [f["pts_ms"] for f in new[0]["frames"]] == [round(next_t * 1000)]


def test_reset_clears_real_fire_hold(tmp_path):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    flame = Flame((1, 1, 30, 30), 0.9)
    for i in range(10):
        assert not collector.process(sample(i / 10, flames=[flame]))
    collector.reset()
    for i in range(10):
        assert not collector.process(sample(i / 10, session=1, flames=[flame]))
    assert collector.finish() == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_reject_nonfinite_time_confidence_details(tmp_path, monkeypatch, bad):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    with pytest.raises(ValueError):
        collector.process(replace(sample(0), t=bad))
    inject(collector, monkeypatch, lambda s: [fire_signal(confidence=bad)])
    with pytest.raises(ValueError):
        collector.process(sample(0))
    collector.reset()
    inject(collector, monkeypatch, lambda s: [fire_signal(details={"nested": [bad]})])
    with pytest.raises(ValueError):
        collector.process(sample(0))
    assert not list(tmp_path.glob("*/manifest.json"))


def test_jpeg_encode_failure_is_explicit(tmp_path, monkeypatch):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    monkeypatch.setattr(cv2, "imencode", lambda *args: (False, None))
    with pytest.raises(OSError):
        collector.process(sample(0))
    assert not list(tmp_path.glob("*/manifest.json"))


@pytest.mark.parametrize("suffix", [".jpg", "manifest.json"])
def test_persistence_failure_no_success_manifest(tmp_path, monkeypatch, suffix):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    inject(collector, monkeypatch, lambda s: [fire_signal()])
    collector.process(sample(0))
    original = module._atomic_bytes

    def fail(path, content):
        if str(path).endswith(suffix):
            raise OSError("disk unavailable")
        original(path, content)

    monkeypatch.setattr(module, "_atomic_bytes", fail)
    with pytest.raises(OSError):
        collector.finish()
    assert not list(tmp_path.glob("*/manifest.json"))
    monkeypatch.setattr(module, "_atomic_bytes", original)
    assert_published(collector.finish()[0], tmp_path)


@pytest.fixture
def cli(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("scan_safety_video", SCRIPT)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    import ai_service.models as models_module
    import ai_service.runtime_env as runtime_env
    configured, inferred = [], []
    monkeypatch.setattr(runtime_env, "configure_model_runtime", lambda: configured.append(True))

    class Models:
        def __init__(self, config):
            assert configured
            print("untrusted library stdout source=private-secret")

        def infer(self, image):
            inferred.append(image)
            print("untrusted inference stdout")
            return [], [Flame((1, 1, 30, 30), 0.9)]

    monkeypatch.setattr(models_module, "LocalModels", Models)
    cli.inferred = inferred
    return cli


def fake_video(tmp_path, monkeypatch, count=31, fps=10, expected=None, fail_at=None):
    source = tmp_path / "source-private-secret.mp4"
    source.write_bytes(b"fake local video")

    class Capture:
        def __init__(self, path):
            self.i = 0
            self.released = False

        def isOpened(self):
            return True

        def get(self, prop):
            return fps if prop == cv2.CAP_PROP_FPS else (count if expected is None else expected)

        def read(self):
            if self.i == fail_at:
                raise KeyboardInterrupt("private-secret")
            if self.i >= count:
                return False, None
            self.i += 1
            return True, sample(0).image

        def release(self):
            self.released = True

    captures = []

    def factory(path):
        captures.append(Capture(path))
        return captures[-1]

    monkeypatch.setattr(cv2, "VideoCapture", factory)
    return source, captures


def test_cli_natural_eof_full_manifest_and_json_stdout(cli, tmp_path, monkeypatch, capsys):
    source, captures = fake_video(tmp_path, monkeypatch)
    output = tmp_path / "scan"
    config = Config()
    config.recording.enabled = True
    report = cli.scan_video(source, config, output)
    assert report["version"] == 1 and report["completed"] is True
    assert report["frames"] == len(cli.inferred) == 31
    assert captures[0].released
    assert json.loads((output / "scan.json").read_text("utf-8")) == report
    assert len(report["candidates"]) == 1
    assert report["candidates"][0]["t"] == 1.0
    assert_published(report["candidates"][0], output)
    stdout = capsys.readouterr().out
    assert "private-secret" not in stdout
    assert all(json.loads(line)["status"] == "progress" for line in stdout.splitlines())


@pytest.mark.parametrize("limit", [1, 12, 31])
def test_cli_limit_is_partial_even_at_exact_length_and_flushes(cli, tmp_path, monkeypatch, limit):
    source, captures = fake_video(tmp_path, monkeypatch)
    output = tmp_path / "scan"
    result = cli.scan_video(source, Config(), output, max_frames=limit)
    assert result["frames"] == limit and not result["completed"]
    assert captures[0].released
    if limit >= 12:
        assert len(result["candidates"]) == 1
        assert_published(result["candidates"][0], output)


def test_cli_preserves_existing_output(cli, tmp_path, monkeypatch):
    source, _ = fake_video(tmp_path, monkeypatch)
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "scan.json"
    marker.write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        cli.scan_video(source, Config(), output)
    assert marker.read_text("utf-8") == "keep me" and not cli.inferred


@pytest.mark.parametrize("fps,count,expected", [(float("nan"), 5, 5), (0, 5, 5),
                                               (10, 0, 0), (10, 15, 30)])
def test_cli_bad_video_cannot_be_complete(cli, tmp_path, monkeypatch, fps, count, expected):
    source, captures = fake_video(tmp_path, monkeypatch, count=count, fps=fps, expected=expected)
    output = tmp_path / "scan"
    with pytest.raises(ValueError):
        cli.scan_video(source, Config(), output)
    assert captures[0].released
    assert json.loads((output / "scan.json").read_text("utf-8"))["completed"] is False


def test_cli_soft_cancel_flushes_partial(cli, tmp_path, monkeypatch):
    source, captures = fake_video(tmp_path, monkeypatch, fail_at=12)
    output = tmp_path / "scan"
    with pytest.raises(KeyboardInterrupt):
        cli.scan_video(source, Config(), output)
    report = json.loads((output / "scan.json").read_text("utf-8"))
    assert captures[0].released and report["frames"] == 12 and not report["completed"]
    assert len(report["candidates"]) == 1
    assert_published(report["candidates"][0], output)


def test_cli_manifest_failure_leaves_scan_incomplete(cli, tmp_path, monkeypatch):
    source, _ = fake_video(tmp_path, monkeypatch)
    original = module._atomic_bytes

    def fail(path, content):
        if path.name == "manifest.json":
            raise OSError("private-secret")
        original(path, content)

    monkeypatch.setattr(module, "_atomic_bytes", fail)
    output = tmp_path / "scan"
    with pytest.raises(OSError):
        cli.scan_video(source, Config(), output)
    assert json.loads((output / "scan.json").read_text("utf-8"))["completed"] is False
    assert not list(output.glob("*/manifest.json"))


def test_cli_main_error_and_completion_metadata_do_not_leak(cli, tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    args = ["--source", "rtsp://user:private-secret@camera/", "--config", str(config),
            "--output", str(tmp_path / "scan")]
    assert cli.main(args) == 1
    stdout = capsys.readouterr().out
    assert "private-secret" not in stdout and "rtsp" not in stdout
    assert json.loads(stdout)["status"] == "failed"
    source, _ = fake_video(tmp_path, monkeypatch)
    args[1] = str(source)
    assert cli.main(args) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[-1]["status"] == "done" and rows[-1]["completed"]
    assert rows[-1]["frames"] == 31


def test_cli_argparse_redacts_invalid_values(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--source", "secret", "--config", "secret", "--output", "secret",
                  "--max-frames", "private-secret"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "private-secret" not in captured.out + captured.err


def test_cli_real_subprocess_hard_terminate_keeps_partial_scan(tmp_path):
    # Stub only model loading; exercise actual CLI process, signal/termination,
    # JPEG persistence and OpenCV video decoding without a model/network call.
    source = tmp_path / "input.avi"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    assert writer.isOpened()
    for _ in range(25):
        writer.write(sample(0).image)
    writer.release()
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    output = tmp_path / "scan"
    runner = """
import runpy, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
import ai_service.models as models
import ai_service.runtime_env as runtime
runtime.configure_model_runtime = lambda: None
class SlowModels:
    def __init__(self, config):
        pass
    def infer(self, image):
        time.sleep(30)
        return [], []
models.LocalModels = SlowModels
script = sys.argv.pop(1)
sys.argv[0] = script
runpy.run_path(script, run_name='__main__')
"""
    process = subprocess.Popen([sys.executable, "-B", "-c", runner,
                                str(SCRIPT.parents[1] / "ai-service"), str(SCRIPT),
                                "--source", str(source), "--config", str(config),
                                "--output", str(output)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not (output / "scan.json").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (output / "scan.json").is_file()
        process.terminate()
        process.communicate(timeout=10)
        assert process.returncode != 0
        assert json.loads((output / "scan.json").read_text("utf-8"))["completed"] is False
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

@pytest.mark.parametrize("fps", [10, 20, 30, 40])
def test_four_fps_clock_does_not_drift_with_source_rate(tmp_path, monkeypatch, fps):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    inject(collector, monkeypatch, lambda s: [fire_signal()] if s.t == 2 else [])
    ready = []
    for i in range(3 * fps + 1):
        ready.extend(collector.process(sample(i / fps)))
    assert len(ready) == 1
    times = [f["pts_ms"] for f in ready[0]["frames"]]
    assert len(times) == 12 and times[0] == 0 and times[-1] < 3000
    assert [t // 250 for t in times] == list(range(12))


@pytest.mark.parametrize("expected", [0, float("nan"), float("inf")])
def test_cli_unknown_video_length_never_asserts_completion(cli, tmp_path, monkeypatch, expected):
    source, _ = fake_video(tmp_path, monkeypatch, expected=expected)
    result = cli.scan_video(source, Config(), tmp_path / "scan")
    assert result["frames"] == 31 and result["completed"] is False


def test_cli_real_video_decoding_with_mock_models(cli, tmp_path):
    source = tmp_path / "input.avi"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 20, (64, 48))
    assert writer.isOpened()
    for i in range(51):
        writer.write(sample(i / 20).image)
    writer.release()
    output = tmp_path / "scan"
    result = cli.scan_video(source, Config(), output)
    assert result["completed"] is True and result["frames"] == 51
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["t"] == 1.0
    assert len(result["candidates"][0]["frames"]) == 8
    assert_published(result["candidates"][0], output)


def test_atomic_manifest_rename_failure_is_not_a_commit(tmp_path, monkeypatch):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    inject(collector, monkeypatch, lambda s: [fire_signal()])
    collector.process(sample(0))
    original = module.os.replace

    def fail(source, destination):
        if destination.name == "manifest.json":
            raise OSError("rename failed")
        original(source, destination)

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        collector.finish()
    assert not list(tmp_path.glob("*/manifest.json"))
    assert len(list(tmp_path.glob("*/manifest.json.*.partial"))) == 1


def test_recovered_posture_keeps_earlier_descent_without_extra_cloud_images(tmp_path, monkeypatch):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    signal = fire_signal(event_type='suspected_fall', details={'fall_transition_observed': False})
    inject(collector, monkeypatch, lambda s: [signal] if s.t == 4 else [])
    ready = []
    for i in range(201):
        ready.extend(collector.process(sample(i / 40)))
    assert len(ready) == 1
    frames = ready[0]['frames']
    assert len(frames) == 12
    times = [f['pts_ms'] for f in frames]
    assert times == [0, 500, 1000, 1500, 2250, 2750, 3250, 3750, 4000, 4250, 4500, 4750]
    assert_published(ready[0], tmp_path)

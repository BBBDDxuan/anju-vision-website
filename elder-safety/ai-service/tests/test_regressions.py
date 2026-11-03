from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import cv2
import httpx
import numpy as np
import pytest

from ai_service.cli import main
from ai_service.config import Config, FallConfig, ModelConfig
from ai_service.models import LocalModels, ModelConfigurationError
from ai_service.network import BackendClient, DeliveryError, DeliveryWorker
from ai_service.sources import mock_pose, mock_samples
from ai_service.spool import Spool
from ai_service.temporal import FallDetector


def event():
    return {"event_id": "local-event", "device_id": "test-device", "event_type": "suspected_fall",
            "severity": "critical", "confidence": 0.9, "track_id": "person-1",
            "occurred_at": datetime.now(timezone.utc).isoformat(), "details": {"simulated": True}}


def jpg(root):
    path = root / "key.jpg"
    ok, encoded = cv2.imencode(".jpg", np.zeros((32, 32, 3), np.uint8))
    assert ok
    path.write_bytes(encoded.tobytes())
    return path


def client(handler):
    return BackendClient("http://local.test", "test-device", "test-secret", Config(),
                         transport=httpx.MockTransport(handler))


def test_canonical_dedup_links_both_media_and_survives_restart(tmp_path):
    uploads, associations, event_posts = [], [], []
    def handler(request):
        if request.url.path == "/api/v1/evidence":
            uploads.append(request.content)
            return httpx.Response(200, json={"evidence_id": f"media-{len(uploads)}"})
        if request.url.path == "/api/v1/events":
            event_posts.append(request.content)
            return httpx.Response(200, json={"event_id": "canonical-event", "deduplicated": True,
                                           "device_id": "test-device"})
        if request.url.path.endswith("/evidence"):
            assert request.url.path == "/api/v1/events/canonical-event/evidence"
            associations.append(json.loads(request.content))
            return httpx.Response(200, json={"event_id": "canonical-event", **associations[-1]})
        return httpx.Response(200)
    spool = Spool(tmp_path, "test-device", "http://local.test", 50_000_000)
    spool.add_event(event(), jpg(tmp_path))
    transport = client(handler)
    worker = DeliveryWorker(spool, transport, Config(), clock=lambda: 100)
    worker.tick()
    assert spool.event("local-event")["backend_event_id"] == "canonical-event"
    frozen = spool.event("local-event")["frozen_payload"]
    transport.close()
    spool.close()
    spool = Spool(tmp_path, "test-device", "http://local.test", 50_000_000)
    transport = client(handler)
    worker = DeliveryWorker(spool, transport, Config(), clock=lambda: 101)
    worker.tick()
    # MP4 signature fixture only: real encoding/decoding is covered by pipeline tests.
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"0"*20)
    spool.attach_clip("local-event", clip, "complete")
    worker.tick()
    assert associations == [{"evidence_id": "media-1"}, {"clip_evidence_id": "media-2"}]
    assert spool.pending_count() == 0 and spool.blocked_count() == 0
    row = spool.event("local-event")
    assert row["keyframe_status"] == row["clip_status"] == "linked"
    assert row["frozen_payload"] == frozen and len(event_posts) == 1
    transport.close()
    spool.close()


@pytest.mark.parametrize("failure", ["timeout", "http_503"])
def test_association_retry_reuses_upload_id_across_restart(tmp_path, failure):
    uploads, associations = [], []
    def handler(request):
        if request.url.path == "/api/v1/evidence":
            uploads.append(request.content)
            return httpx.Response(200, json={"evidence_id": "saved-upload"})
        if request.url.path == "/api/v1/events":
            return httpx.Response(200, json={"event_id": "canonical-event", "deduplicated": True})
        if request.url.path.endswith("/evidence"):
            associations.append(request.content)
            if len(associations) == 1:
                if failure == "timeout":
                    raise httpx.ReadTimeout("private-response", request=request)
                return httpx.Response(503)
            return httpx.Response(200, json={"event_id": "canonical-event", "evidence_id": "saved-upload"})
        return httpx.Response(200)
    spool = Spool(tmp_path, "test-device", "http://local.test", 50_000_000)
    spool.add_event(event(), jpg(tmp_path))
    transport = client(handler)
    worker = DeliveryWorker(spool, transport, Config(), clock=lambda: 100)
    worker.tick()
    worker.tick()
    assert spool.pending_count() == 1
    transport.close()
    spool.close()
    spool = Spool(tmp_path, "test-device", "http://local.test", 50_000_000)
    transport = client(handler)
    DeliveryWorker(spool, transport, Config(), clock=lambda: 101).tick()
    assert len(uploads) == 1 and associations[0] == associations[1]
    assert spool.pending_count() == 0
    transport.close()
    spool.close()


@pytest.mark.parametrize("status", [403, 404, 409])
def test_permanent_association_failure_is_recorded_without_infinite_retry(tmp_path, status, caplog):
    associations = []
    def handler(request):
        if request.url.path == "/api/v1/evidence":
            return httpx.Response(200, json={"evidence_id": "saved-upload"})
        if request.url.path == "/api/v1/events":
            return httpx.Response(200, json={"event_id": "canonical-event", "deduplicated": True})
        if request.url.path.endswith("/evidence"):
            associations.append(request.content)
            return httpx.Response(status, text="private-response-do-not-log")
        return httpx.Response(200)
    spool = Spool(tmp_path, "test-device", "http://local.test", 50_000_000)
    keyframe = jpg(tmp_path)
    content = keyframe.read_bytes()
    spool.add_event(event(), keyframe)
    transport = client(handler)
    worker = DeliveryWorker(spool, transport, Config(), clock=lambda: 100)
    worker.tick()
    worker.tick()
    assert spool.pending_count() == 0 and spool.blocked_count() == 1
    assert spool.event("local-event")["keyframe_status"] == "association_blocked"
    assert spool.event("local-event")["evidence_id"] == "saved-upload"
    assert keyframe.read_bytes() == content
    assert "manual review" in caplog.text and "private-response" not in caplog.text
    transport.close()
    spool.close()
    spool = Spool(tmp_path, "test-device", "http://local.test", 50_000_000)
    transport = client(handler)
    DeliveryWorker(spool, transport, Config(), clock=lambda: 1000).tick()
    assert len(associations) == 1 and spool.blocked_count() == 1
    transport.close()
    spool.close()


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_different_event_id_requires_literal_deduplicated_true(flag):
    transport = client(lambda r: httpx.Response(200, json={"event_id": "other", "deduplicated": flag}))
    with pytest.raises(DeliveryError, match="event_id_mismatch"):
        transport.send_event(event())
    transport.close()


def test_dedup_response_for_another_device_is_rejected():
    transport = client(lambda r: httpx.Response(200, json={"event_id": "other", "deduplicated": True, "device_id": "other-device"}))
    with pytest.raises(DeliveryError, match="event_device_mismatch"):
        transport.send_event(event())
    transport.close()


def test_legacy_late_media_is_requeued_for_association(tmp_path):
    db = sqlite3.connect(tmp_path / "spool.sqlite3")
    db.executescript("""
        CREATE TABLE events(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL,frozen_payload TEXT,
            keyframe TEXT,evidence_id TEXT,delivered INTEGER NOT NULL DEFAULT 0,clip_path TEXT,
            clip_evidence_id TEXT,clip_status TEXT NOT NULL DEFAULT 'collecting',clip_note TEXT);
        CREATE TABLE jobs(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,kind TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,last_error TEXT,UNIQUE(event_id,kind));
    """)
    db.execute("INSERT INTO events(event_id,payload,frozen_payload,delivered,clip_evidence_id,clip_status) VALUES(?,?,?,1,?,'uploaded_local_association')",
               ("local-event", json.dumps(event()), json.dumps(event()), "old-clip"))
    db.execute("INSERT INTO jobs(event_id,kind,done) VALUES('local-event','clip',1)")
    db.commit()
    db.close()
    spool = Spool(tmp_path, "test-device", "http://local.test", 50_000_000)
    assert spool.event("local-event")["backend_event_id"] == "local-event"
    assert spool.due(100)["kind"] == "clip"
    linked = []
    def handler(request):
        if request.url.path.endswith("/evidence"):
            linked.append(json.loads(request.content))
            return httpx.Response(200, json={"event_id": "local-event", "clip_evidence_id": "old-clip"})
        assert request.url.path != "/api/v1/events"
        return httpx.Response(200)
    transport = client(handler)
    DeliveryWorker(spool, transport, Config(), clock=lambda: 100).tick()
    assert linked == [{"clip_evidence_id": "old-clip"}]
    assert spool.event("local-event")["clip_status"] == "linked"
    transport.close()
    spool.close()


def test_fall_prone_hold_is_broken_by_a_single_upright_frame():
    poses = [mock_pose() for _ in range(20)]
    poses += [mock_pose(p) for p in (0.25, 0.5, 0.75, 1)]
    poses += [mock_pose(1) for _ in range(5)] + [mock_pose()]
    poses += [mock_pose(1) for _ in range(15)]
    detector = FallDetector(FallConfig())
    alerts = [(i/10, signal) for i, pose in enumerate(poses) if (signal := detector.update("p", pose, i/10))]
    assert len(alerts) == 1 and alerts[0][0] >= 3.8


def test_missing_weights_cli_rejected_before_spool_or_network(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SAFETY_DEVICE_ID", "test-device")
    monkeypatch.setenv("SAFETY_DEVICE_TOKEN", "test-secret")
    import ai_service.cli as cli
    monkeypatch.setattr(cli, "BackendClient", lambda *a, **kw: pytest.fail("must not start network"))
    assert main(["--source", "missing.mp4", "--backend", "http://local.test", "--state-dir", str(tmp_path)]) == 2
    assert "no local weights" in caplog.text and not (tmp_path / "spool.sqlite3").exists()


def test_pose_weights_require_license_metadata_without_loading(tmp_path):
    weights = tmp_path / "local-pose.pt"
    weights.write_bytes(b"test-placeholder")
    with pytest.raises(ModelConfigurationError, match="pose_license.name"):
        LocalModels(ModelConfig(pose_weights=str(weights)), factory=lambda *a, **kw: pytest.fail("must not load"))


def test_pytest_shared_root_is_accepted_without_creating_basetemp():
    hook = runpy.run_path(str(Path(__file__).with_name("conftest.py")))["pytest_configure"]
    target = Path(__file__).resolve().parents[2] / "runtime" / f"pytest-{uuid.uuid4().hex}"
    config = SimpleNamespace(option=SimpleNamespace(basetemp=str(target)))
    hook(config)
    assert Path(config.option.basetemp) == target and not target.exists()


def test_pytest_existing_temp_is_preserved(tmp_path):
    hook = runpy.run_path(str(Path(__file__).with_name("conftest.py")))["pytest_configure"]
    keep = tmp_path / "keep.txt"
    keep.write_text("existing test data")
    config = SimpleNamespace(option=SimpleNamespace(basetemp=str(tmp_path)))
    with pytest.raises(pytest.UsageError):
        hook(config)
    assert keep.read_text() == "existing test data"

"""Reviewed JPEG relay uses the real API with synthetic pixels and offline transport."""
from datetime import datetime, timezone
from pathlib import Path
import importlib.util

import cv2
import httpx
import numpy as np
from fastapi.testclient import TestClient

from ai_service.config import Config
from ai_service.preview_alerts import PreviewAlerts
from ai_service.types import Signal
from safety_backend.config import Settings
from safety_backend.main import create_app


def test_reviewed_jpeg_upload_event_association_and_source_file_independence(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / 'safety', user_token='user-' + 'a' * 32,
        device_tokens={'camera_001': 'device-' + 'b' * 32}, worker_enabled=False))
    frame = tmp_path / 'candidate.jpg'
    cv2.imwrite(str(frame), np.zeros((40, 40, 3), np.uint8))
    with TestClient(app) as client:
        def offline_transport(request):
            return client.request(request.method, request.url.path,
                                  headers=dict(request.headers), content=request.content)
        relay = PreviewAlerts(Config(), tmp_path / 'spool', 'camera_001', 'http://test-only.local',
            'device-' + 'b' * 32, transport=httpx.MockTransport(offline_transport), start=False)
        try:
            signal = Signal('help_requested', 'track-1', .9, {'review_status': 'confirmed',
                'review_model': 'synthetic-proof-only', 'cloud_images': True})
            identifier = relay.alarm(signal, datetime.now(timezone.utc), keyframe=frame)
            frame.unlink()
            assert Path(relay.spool.event(identifier)['keyframe']).is_file()
            relay.delivery.tick()
            record = app.state.repo.get_event(identifier)
            assert record['evidence_id']
            evidence = app.state.repo.get_evidence(record['evidence_id'])
            assert Path(evidence['path']).read_bytes().startswith(b'\xff\xd8\xff')
            assert record['details']['review_status'] == 'confirmed'
        finally:
            relay.close()


def test_worker_passes_a_frame_selected_by_the_review(tmp_path):
    script = Path(__file__).resolve().parents[2] / 'scripts/web-camera-worker.py'
    spec = importlib.util.spec_from_file_location('reviewed_evidence_worker', script)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    captured = []
    class Relay:
        def alarm(self, signal, when, keyframe=None):
            captured.append(keyframe)
            return 'synthetic-event'
    candidate = {'id': 'candidate', 'event_type': 'help_requested', 'track_id': 't',
        'confidence': .9, 'occurred_at': '2026-11-04T00:00:00+00:00',
        'frames': [{'id': 'unselected', 'path': str(tmp_path / 'other.jpg')},
                   {'id': 'selected', 'path': str(tmp_path / 'selected.jpg')}]}
    result = {'candidate_id': 'candidate', 'verdict': 'confirmed', 'model': 'synthetic-only',
              'cloud_images': True, 'frame_ids': ['selected']}
    worker.apply_review_result(candidate, {}, result, Relay())
    assert captured == [tmp_path / 'selected.jpg']

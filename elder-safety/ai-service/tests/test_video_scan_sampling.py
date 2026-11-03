"""Sampling and resumable evidence tests; inference is synthetic and offline."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import hashlib

import cv2
import numpy as np
import pytest

from ai_service.config import Config
from ai_service.types import Flame
import ai_service.models as models_module
import ai_service.runtime_env as runtime_env

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/scan-safety-video.py'


@pytest.fixture
def scanner(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('sampled_video_scanner', SCRIPT)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(runtime_env, 'configure_model_runtime', lambda: None)
    cli.inferences = []
    class Models:
        def __init__(self, config):
            pass
        def infer(self, image):
            cli.inferences.append(True)
            return [], [Flame((1, 1, 30, 30), .9)]
    monkeypatch.setattr(models_module, 'LocalModels', Models)
    class Capture:
        def __init__(self, path):
            self.index = 0
        def isOpened(self):
            return True
        def get(self, prop):
            return 30.0 if prop == cv2.CAP_PROP_FPS else 1000
        def read(self):
            if self.index == 1000:
                return False, None
            self.index += 1
            return True, np.zeros((48, 64, 3), np.uint8)
        def release(self):
            pass
    monkeypatch.setattr(cv2, 'VideoCapture', Capture)
    cli.source = tmp_path / 'source.mp4'
    cli.source.write_bytes(b'synthetic-local-video-no-models')
    return cli


def test_30fps_source_uses_10fps_and_preserves_temporal_cooldown(scanner, tmp_path, monkeypatch):
    import ai_service.safety_candidates as collector
    published = []
    real = collector.atomic_json
    def publish(path, value):
        if path.name == 'scan.json':
            published.append(json.loads(json.dumps(value)))
        real(path, value)
    monkeypatch.setattr(collector, 'atomic_json', publish)
    config = Config()
    before = asdict(config)
    result = scanner.scan_video(scanner.source, config, tmp_path / 'scan')
    assert result['completed'] and result['frames'] == 1000
    assert result['inferred_frames'] == len(scanner.inferences) == 334
    assert result['progress'] == 100 and result['processed_frames'] == 1000
    assert result['scan_policy']['stride'] == 3
    assert [c['t'] for c in result['candidates']] == [1.0, 16.0, 31.0]
    assert all(c['details']['frames'] >= 5 for c in result['candidates'])
    assert any(r['candidates'] and not r['completed'] and r['frames'] < 1000 for r in published)
    assert asdict(config) == before


def test_budget_pause_rescan_freezes_exact_candidate_and_frame_ids(scanner, tmp_path):
    output = tmp_path / 'scan'
    first = scanner.scan_video(scanner.source, Config(), output, max_frames=47)
    assert not first['completed'] and len(first['candidates']) == 1
    old = first['candidates'][0]
    before = {f['id']: hashlib.sha256(Path(f['path']).read_bytes()).hexdigest() for f in old['frames']}
    # Stand-in for persisted paid review. Resumed candidate must hit this key.
    paid_reviews = {old['id']: {'frame_ids': [f['id'] for f in old['frames']]}}
    charges = 1
    resumed = scanner.scan_video(scanner.source, Config(), output, resume=True)
    same = next(c for c in resumed['candidates'] if c['id'] == old['id'])
    assert same == old
    assert {f['id']: hashlib.sha256(Path(f['path']).read_bytes()).hexdigest() for f in same['frames']} == before
    for c in resumed['candidates']:
        if c['id'] not in paid_reviews:
            charges += 1
            paid_reviews[c['id']] = {'frame_ids': [f['id'] for f in c['frames']]}
    assert charges == len(resumed['candidates']) == 3
    again = scanner.scan_video(scanner.source, Config(), output, resume=True)
    assert again['candidates'] == resumed['candidates']
    assert len({c['id'] for c in again['candidates']}) == 3


def test_frequency_change_cannot_reuse_partial_evidence(scanner, tmp_path):
    output = tmp_path / 'scan'
    scanner.scan_video(scanner.source, Config(), output, max_frames=47)
    before = (output / 'scan.json').read_bytes()
    with pytest.raises(ValueError, match='fingerprint mismatch'):
        scanner.scan_video(scanner.source, Config(), output, scan_fps=30, resume=True)
    assert (output / 'scan.json').read_bytes() == before


def test_resume_rejects_missing_evidence(scanner, tmp_path):
    output = tmp_path / 'scan'
    partial = scanner.scan_video(scanner.source, Config(), output, max_frames=47)
    Path(partial['candidates'][0]['frames'][0]['path']).unlink()
    with pytest.raises((ValueError, OSError)):
        scanner.scan_video(scanner.source, Config(), output, resume=True)


def test_sampling_adapts_to_gap_and_minframes_without_changing_config(scanner):
    config = Config()
    config.help.min_frames = 12
    assert scanner.sampling_stride(30, config) == 2
    config.help.min_frames = 5
    config.tracking.max_gap_seconds = .05
    assert scanner.sampling_stride(30, config) == 1
    config.tracking.max_gap_seconds = .3
    assert scanner.sampling_stride(8, config) == 1
    assert scanner.sampling_stride(30, config, 30) == 1
    for value in (False, 0, -1, float('nan'), 241):
        with pytest.raises(ValueError):
            scanner.sampling_stride(30, config, value)

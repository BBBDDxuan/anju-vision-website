from __future__ import annotations

from dataclasses import replace
import threading

import numpy as np
import pytest

from ai_service.config import Config, FallConfig, FireConfig
from ai_service.engine import DetectionEngine
from ai_service.sources import mock_pose, mock_samples
from ai_service.temporal import FallDetector, FireDetector
from ai_service.tracking import Tracker
from ai_service.types import Flame, Pose, iou


def sequence(detector, poses, step=0.1, track="p"):
    return [s for index, pose in enumerate(poses) if (s := detector.update(track, pose, index*step))]


def upright(n=20):
    return [mock_pose() for _ in range(n)]


def drop():
    return [mock_pose(p) for p in (0.25, 0.5, 0.75, 1.0)] + [mock_pose(1) for _ in range(20)]


def test_mock_has_exactly_two_explicit_simulated_events():
    engine = DetectionEngine(Config())
    samples = list(mock_samples(threading.Event(), realtime=False))
    signals = [(s, signal) for s in samples for signal in engine.process(s)]
    assert [s.event_type for _, s in signals] == ["suspected_fall", "fire_detected"]
    assert all(sample.simulated and sample.occurred_at.utcoffset() is not None for sample, _ in signals)
    assert len({s.track_id for _, s in signals}) == 2


def test_fall_requires_standing_fast_drop_and_hold():
    signals = sequence(FallDetector(FallConfig()), upright()+drop())
    assert len(signals) == 1
    assert signals[0].details["hold_seconds"] >= 0.8
    assert signals[0].details["peak_drop_speed"] >= 0.6


@pytest.mark.parametrize("poses", [
    [mock_pose(1)],
    [mock_pose(1) for _ in range(50)],
    upright()+[mock_pose(1)]+upright(),
    upright()+[mock_pose(p) for p in np.linspace(0, 1, 70)]+[mock_pose(1)]*20,
    # Sit: hip drops, but torso stays upright and bbox remains tall.
    upright()+[mock_pose(0, hip_y=260+10*i) for i in range(1, 11)],
    # Bend: horizontal torso with no hip drop.
    upright()+[mock_pose(p, hip_y=260) for p in np.linspace(0, 1, 5)]+[mock_pose(1, hip_y=260)]*20,
])
def test_nonfalls_do_not_alert(poses):
    assert not sequence(FallDetector(FallConfig()), poses)


def test_missing_keypoints_disarm_candidate():
    poses = upright()+drop()
    kp = poses[24].keypoints.copy()
    kp[11, 2] = 0.1
    poses[24] = replace(poses[24], keypoints=kp)
    assert not sequence(FallDetector(FallConfig()), poses)


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_nonfinite_keypoints_do_not_alert(bad):
    detector = FallDetector(FallConfig())
    sequence(detector, upright())
    p = mock_pose(1)
    p.keypoints[5, 0] = bad
    assert detector.update("p", p, 2) is None
    assert "p" not in detector.states


def test_gap_and_reconnect_disarm_fall_and_change_id():
    engine = DetectionEngine(Config())
    samples = list(mock_samples(threading.Event(), realtime=False))
    for sample in samples[:24]:
        engine.process(sample)
    old_ids = set(engine.tracker.tracks)
    later = replace(samples[24], session=2)
    assert not engine.process(later)
    assert old_ids.isdisjoint(engine.tracker.tracks)
    for sample in samples[25:40]:
        assert not engine.process(replace(sample, session=2))


def test_absent_person_cannot_bridge_fall_candidate():
    engine = DetectionEngine(Config())
    samples = list(mock_samples(threading.Event(), realtime=False))
    for sample in samples[:24]:
        engine.process(sample)
    engine.process(replace(samples[24], poses=[]))
    assert not engine.fall.states
    assert not [signal for sample in samples[25:40] for signal in engine.process(sample)]


def test_tracker_one_to_one_and_expiry():
    tracker = Tracker(Config().tracking)
    first = tracker.update([(0, 0, 10, 30), (50, 0, 60, 30)], 0)
    second = tracker.update([(51, 0, 61, 30), (1, 0, 11, 30)], 0.1)
    assert second[0] == first[1] and second[1] == first[0]
    assert len(set(second.values())) == 2
    third = tracker.update([(1, 0, 11, 30)], 1)
    assert third[0] not in first.values()
    assert tracker.update([(float("nan"), 0, 10, 30)], 1.1) == {}


def test_fire_needs_time_count_and_same_region():
    detector = FireDetector(FireConfig())
    region = Flame((10, 10, 60, 90), 0.9)
    assert not detector.update([region], 0)
    signals = [s for i in range(1, 31) for s in detector.update([region], i/10)]
    assert len(signals) == 1
    assert signals[0].details["frames"] >= 5
    assert signals[0].details["hold_seconds"] >= 1


@pytest.mark.parametrize("scenario", ["flicker", "moving", "low_confidence", "time_gap"])
def test_unstable_fire_no_alert(scenario):
    detector = FireDetector(FireConfig())
    results = []
    for i in range(20):
        x = i*100 if scenario == "moving" else 10
        detections = [Flame((x, 10, x+50, 90), 0.2 if scenario == "low_confidence" else 0.9)]
        if scenario == "flicker" and i % 2:
            detections = []
        t = i * (0.5 if scenario == "time_gap" else 0.1)
        results += detector.update(detections, t)
    assert not results


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0


def test_standing_reference_is_not_dragged_down_by_slow_initial_tilt():
    detector = FallDetector(FallConfig())
    for i in range(20):
        assert detector.update("p", mock_pose(0, hip_y=260 + i), i / 10) is None
    assert detector.states["p"].anchor_y == 260
    assert detector.states["p"].armed_at is not None

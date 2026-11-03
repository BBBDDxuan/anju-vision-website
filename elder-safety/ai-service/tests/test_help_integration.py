from dataclasses import replace
from datetime import datetime, timezone

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.sources import mock_pose
from ai_service.types import Sample
import numpy as np
import pytest


def sample(t, raised=True, session=0, gesture="extended"):
    pose = mock_pose()
    kp = pose.keypoints.copy()
    kp[[5, 6], :2] = ((240, 160), (300, 160))
    kp[[9, 10], :2] = ((220, 80 if raised else 220),
                       (320, 80 if raised else 220))
    kp[[7, 8], :2] = (kp[[5, 6], :2] + kp[[9, 10], :2]) / 2
    if gesture == "hold_head":
        kp[[7, 8], :2] = ((220, 100), (320, 100))
        kp[[9, 10], :2] = ((265, 105), (275, 105))
    elif gesture == "touch_head":
        kp[9, :2] = (270, 105)
        kp[7, :2] = (kp[5, :2] + kp[9, :2]) / 2
    elif gesture != "extended":
        raise AssertionError(gesture)
    pose = replace(pose, bbox=(210, 60, 330, 400), keypoints=kp)
    return Sample(np.zeros((480, 640, 3), dtype=np.uint8), t,
                  datetime.now(timezone.utc), session=session, poses=[pose])


def test_engine_raises_help_after_continuous_realistic_track():
    engine = DetectionEngine(Config())
    signals = [s for i in range(30) for s in engine.process(sample(i / 10))]
    assert [s.event_type for s in signals] == ["help_requested"]
    assert signals[0].details["hold_seconds"] >= 1
    assert signals[0].details["algorithm"] == "temporal_two_hands_raised_extended_v2"


def test_session_switch_and_absence_do_not_bridge_help_hold():
    engine = DetectionEngine(Config())
    for i in range(9):
        assert not engine.process(sample(i / 10))
    assert not engine.process(sample(0.9, session=1))
    assert not engine.process(replace(sample(1.0, session=1), poses=[]))
    for i in range(11, 19):
        assert not engine.process(sample(i / 10, session=1))


@pytest.mark.parametrize("gesture", ["hold_head", "touch_head"])
def test_engine_rejects_head_contact_even_though_wrists_pass_old_height_rule(gesture):
    engine = DetectionEngine(Config())
    pose = sample(0, gesture=gesture).poses[0]
    reference = pose.keypoints[[0, 5, 6], 1].min()
    margin = (pose.bbox[3] - pose.bbox[1]) * engine.help.config.raise_margin_ratio
    assert (pose.keypoints[[9, 10], 1] < reference - margin).all()
    assert not [s for i in range(40)
                for s in engine.process(sample(i / 10, gesture=gesture))]


@pytest.mark.parametrize("gesture", ["hold_head", "touch_head"])
def test_engine_head_contact_breaks_pending_hold_and_requires_full_recovery(gesture):
    engine = DetectionEngine(Config())
    for i in range(9):
        assert not engine.process(sample(i / 10))
    assert not engine.process(sample(0.9, gesture=gesture))
    for i in range(10, 20):
        assert not engine.process(sample(i / 10))
    signal, = engine.process(sample(2.0))
    assert signal.event_type == "help_requested"
    assert signal.details["frames"] == 11
    assert signal.details["hold_seconds"] == 1


@pytest.mark.parametrize("gesture", ["hold_head", "touch_head"])
def test_engine_head_contact_does_not_clear_previous_alert_cooldown(gesture):
    engine = DetectionEngine(Config())
    signals = [s for i in range(11) for s in engine.process(sample(i / 10))]
    assert [s.event_type for s in signals] == ["help_requested"]
    assert not engine.process(sample(1.1, gesture=gesture))
    for i in range(12, 160):
        assert not engine.process(sample(i / 10))
    signal, = engine.process(sample(16.0))
    assert signal.event_type == "help_requested"


def test_engine_lowered_arms_do_not_alert():
    engine = DetectionEngine(Config())
    assert not [s for i in range(30) for s in engine.process(sample(i / 10, raised=False))]

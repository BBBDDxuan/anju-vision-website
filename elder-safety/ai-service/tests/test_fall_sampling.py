"""Regression coverage for bounded frame jitter and staggered drop evidence."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from ai_service.config import Config, FallConfig
from ai_service.engine import DetectionEngine
from ai_service.sources import mock_pose
from ai_service.temporal import FallDetector
from ai_service.types import Sample


def feed(detector, sequence):
    return [signal for t,pose in sequence if (signal:=detector.update('p',pose,t))]


def test_fast_descent_before_minimum_distance_is_remembered_briefly():
    detector=FallDetector(FallConfig())
    poses=[(i*.1,mock_pose()) for i in range(11)]
    # First: 20px/0.1s = .74 standing heights/s, below min distance.
    # Later: distance crosses threshold during slower descent, then prone hold.
    poses += [(1.1,mock_pose(.45,hip_y=280)),(1.2,mock_pose(.7,hip_y=292)),
              (1.3,mock_pose(1,hip_y=310))]
    poses += [(1.4+i*.1,mock_pose(1,hip_y=310)) for i in range(10)]
    signals=feed(detector,poses)
    assert len(signals)==1
    assert signals[0].details['peak_drop_speed'] >= .6
    assert signals[0].details['algorithm']=='temporal_pose_v2'


def test_fast_descent_evidence_expires_instead_of_arming_later_slow_bend():
    detector=FallDetector(FallConfig(observation_seconds=2.5))
    poses=[(i*.1,mock_pose()) for i in range(11)]
    poses += [(1.1,mock_pose(.45,hip_y=280))]
    # Slow descent crosses the distance threshold well after the motion window.
    poses += [(1.2+i*.1,mock_pose(1,hip_y=281+i*2)) for i in range(20)]
    poses += [(3.2+i*.1,mock_pose(1,hip_y=320)) for i in range(10)]
    assert not feed(detector,poses)


def test_live_config_tolerates_400ms_jitter_but_not_disconnect():
    config=Config.load(str(Path(__file__).parents[1]/'config.team.json'))
    engine=DetectionEngine(config)
    def sample(t,pose,session=1):
        return Sample(np.zeros((2,2,3),np.uint8),t,datetime.now(timezone.utc),
                      poses=[pose],session=session,flames_observed=False)
    for t in [0,.1,.2,.3,.4,.5,.6]: assert not engine.process(sample(t,mock_pose()))
    old_ids=set(engine.tracker.tracks)
    assert not engine.process(sample(1.,mock_pose(.5)))
    assert set(engine.tracker.tracks)==old_ids
    assert engine.fall.states[next(iter(old_ids))].armed_at is not None
    assert not engine.process(sample(1.8,mock_pose(1)))
    assert old_ids.isdisjoint(engine.tracker.tracks)
    assert not engine.fall.states[next(iter(engine.tracker.tracks))].armed_at


def test_diagnostics_report_obscured_keypoints_and_missing_standing_history():
    detector=FallDetector(FallConfig())
    assert not detector.update('p',mock_pose(1),0)
    assert detector.diagnostics['p']['state']=='need_standing'
    pose=mock_pose();pose.keypoints[11,2]=.1
    assert not detector.update('p',pose,.1)
    assert detector.diagnostics['p']['state']=='keypoints_unreliable'
    assert 'p' not in detector.states
    detector.retain(set());assert not detector.diagnostics


def test_rapid_tilt_with_reliable_drop_handles_small_projected_vertical_velocity():
    detector=FallDetector(FallConfig())
    poses=[(i*.1,mock_pose()) for i in range(11)]
    # Vertical speed stays below .6 bbox heights/s; rotation is rapid.
    poses += [(1.1+i*.1,mock_pose((i+1)/4,hip_y=270+i*15)) for i in range(4)]
    poses += [(1.5+i*.1,mock_pose(1,hip_y=315)) for i in range(12)]
    signals=feed(detector,poses)
    assert len(signals)==1
    assert signals[0].details['peak_drop_speed'] < .6
    assert signals[0].details['peak_rotation_speed_deg_s'] >= 120


def test_slow_tilt_and_slow_descent_still_do_not_qualify():
    detector=FallDetector(FallConfig(observation_seconds=2.5))
    poses=[(i*.1,mock_pose()) for i in range(11)]
    poses += [(1.1+i*.1,mock_pose((i+1)/20,hip_y=260+(i+1)*3)) for i in range(20)]
    poses += [(3.1+i*.1,mock_pose(1,hip_y=320)) for i in range(15)]
    assert not feed(detector,poses)

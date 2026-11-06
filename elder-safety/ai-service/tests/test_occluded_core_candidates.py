"""Lower-body confidence loss is not proof of sitting, support loss or a fall."""
import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.safety_candidates import SafetyCandidateCollector
from ai_service.types import Pose
from test_seated_core_candidates import pose, sample


ALGORITHM = 'lower_body_unreliable_core_descent_review_v1'


def hidden_pose(shoulder=150, hip=245, *, core=True):
    current = pose(shoulder, hip, core=core)
    current.keypoints[[13, 14, 15, 16], 2] = 0
    return current


def configuration():
    c = Config()
    c.fall.observation_seconds = 2.5
    return c


def run(fps, action, seconds=4, *, review=True):
    engine = DetectionEngine(configuration(), allow_review_candidates=review)
    events = []
    for i in range(round(seconds * fps) + 1):
        t = i / fps
        events.extend((t, signal) for signal in engine.process(sample(t, action(t))))
    return events


@pytest.mark.parametrize('fps', [5, 7, 10, 12, 15, 30])
def test_missing_legs_from_first_frame_still_proposes_observed_descent(fps):
    events = run(fps, lambda t: hidden_pose() if t < 1 else hidden_pose(220, 280))
    assert len(events) == 1
    t, signal = events[0]
    assert 1.2 - 1e-8 <= t <= 1.6 + 1e-8
    assert signal.details['algorithm'] == ALGORITHM
    assert signal.details['preceding_phase'] == 'stable_core_posture_unknown'
    assert not signal.details['chair_verified']
    assert not signal.details['occlusion_verified']
    assert not signal.details['floor_contact_observed']


def test_slow_sliding_with_missing_legs_uses_frozen_pre_descent_reference():
    def action(t):
        progress = min(1, max(0, (t - 1) / 3))
        return hidden_pose(150 + 70 * progress, 245 + 35 * progress)
    events = run(10, action, 6)
    assert len(events) == 1 and events[0][1].details['algorithm'] == ALGORITHM
    assert events[0][1].details['reference_t'] <= 1.3


@pytest.mark.parametrize('action', [
    lambda t: hidden_pose(),
    lambda t: hidden_pose() if t < 1 else hidden_pose(220, 245),
    lambda t: hidden_pose() if t < 1 else hidden_pose(150, 280),
    lambda t: hidden_pose() if t < 1 else hidden_pose(220, 280, core=False),
    lambda t: hidden_pose() if t < 1 or t > 1.1 else hidden_pose(220, 280),
    lambda t: hidden_pose(220, 280),  # No observed descent on stream startup.
])
def test_static_single_joint_motion_weak_core_and_twitches_do_not_trigger(action):
    assert not run(10, action)


def test_absent_person_breaks_stable_background_and_cannot_supply_descent():
    def action(t):
        if .8 <= t < 1:
            return None
        return hidden_pose() if t < .8 else hidden_pose(220, 280)
    assert not run(10, action)


def test_absent_person_breaks_fresh_descent_tail():
    def action(t):
        if abs(t - 1.1) < 1e-8:
            return None
        return hidden_pose() if t < 1 else hidden_pose(220, 280)
    assert not run(10, action, 1.3)


def test_out_of_frame_core_or_tracking_jump_does_not_supply_evidence():
    def invalid(t):
        current = hidden_pose() if t < 1 else hidden_pose(220, 280)
        if t >= 1:
            current.keypoints[5, 0] = -1
        return current
    assert not run(10, invalid)
    def jump(t):
        current = hidden_pose() if t < 1 else hidden_pose(220, 280)
        if t >= 1:
            current.keypoints[:, 0] += 300
            current = Pose((450, 110, 620, 390), current.keypoints, current.confidence)
        return current
    assert not run(10, jump)


def test_two_overlapping_people_do_not_share_core_background():
    engine = DetectionEngine(configuration(), allow_review_candidates=True)
    signals = []
    for i in range(31):
        t = i / 10
        current = hidden_pose() if t < 1 else hidden_pose(220, 280)
        frame = sample(t, current)
        frame.poses.append(Pose(current.bbox, current.keypoints.copy(), current.confidence))
        signals.extend(engine.process(frame))
    assert not signals


def test_unknown_core_fallback_does_not_replace_reliable_leg_rules():
    events = run(10, lambda t: pose() if t < 1 else pose(220, 280))
    assert events and all(e.details['algorithm'] != ALGORITHM for _, e in events)


def test_reliable_feet_without_knees_keep_existing_support_guards():
    def action(t):
        current = pose() if t < 1 else pose(220, 280)
        current.keypoints[[13, 14], 2] = 0
        return current
    assert not any(e.details['algorithm'] == ALGORITHM for _, e in run(10, action))


def test_flickering_foot_confidence_does_not_erase_continuously_observed_core():
    def action(t):
        current = hidden_pose() if t < 1 else hidden_pose(220, 280)
        if round(t * 10) % 2:
            current.keypoints[[15, 16], 2] = .98
        return current
    events = run(10, action)
    assert len(events) == 1 and events[0][1].details['algorithm'] == ALGORITHM


def test_one_isolated_weak_foot_sample_cannot_establish_unknown_support_background():
    def action(t):
        current = hidden_pose() if t < 1 else hidden_pose(220, 280)
        if abs(t - .3) > 1e-8:
            current.keypoints[[15, 16], 2] = .98
        return current
    assert not any(e.details['algorithm'] == ALGORITHM for _, e in run(10, action))


def test_strict_engine_never_enables_unknown_core_fallback():
    assert not run(10, lambda t: hidden_pose() if t < 1 else hidden_pose(220, 280), review=False)


def test_public_collector_preserves_motion_context_without_legs(tmp_path):
    collector = SafetyCandidateCollector(configuration(), tmp_path)
    events = []
    for i in range(81):
        t = i / 10
        progress = min(1, max(0, (t - 1) / 3))
        events.extend(collector.process(sample(t, hidden_pose(150 + 70 * progress, 245 + 35 * progress))))
    events.extend(collector.finish())
    assert len(events) == 1
    event = events[0]
    assert event['details']['algorithm'] == ALGORITHM
    assert len(event['frames']) <= 12
    times = [f['pts_ms'] for f in event['frames']]
    assert times == sorted(set(times))
    assert times[0] <= event['details']['reference_t'] * 1000
    assert times[-1] >= 4000

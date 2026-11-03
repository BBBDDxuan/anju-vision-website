"""Synthetic geometry/time checks; no camera, model, network, or intent claims."""
from dataclasses import fields, replace
import json

import numpy as np
import pytest

from ai_service.config import update
from ai_service.help_gesture import HelpConfig, HelpGestureDetector
from ai_service.types import Pose, Signal


def make_pose(left_y=60.0, right_y=60.0):
    kp = np.zeros((17, 3), dtype=float)
    kp[:, 2] = 0.9
    kp[0, :2] = (100, 100)
    kp[5, :2] = (60, 150)
    kp[6, :2] = (140, 150)
    kp[9, :2] = (20, left_y)
    kp[10, :2] = (180, right_y)
    # Real straight arms; zero/unset elbows are not a raised-arm fixture.
    kp[7, :2] = (kp[5, :2] + kp[9, :2]) / 2
    kp[8, :2] = (kp[6, :2] + kp[10, :2]) / 2
    return Pose((0, 0, 200, 400), kp, confidence=0.95)


@pytest.fixture
def config():
    return HelpConfig(hold_seconds=0.5, min_frames=3,
                      max_gap_seconds=0.25, cooldown_seconds=2.0)


def feed(detector, times, pose=None, track="person-1"):
    pose = make_pose() if pose is None else pose
    return [signal for t in times
            if (signal := detector.update(track, pose, t)) is not None]


def test_defaults_are_positive_and_existing_update_accepts_config():
    config = HelpConfig()
    assert all(getattr(config, f.name) > 0 for f in fields(config) if f.name != "require_facing_camera")
    assert config.require_facing_camera is False
    update(config, {"hold_seconds": 2, "min_frames": 8, "confidence": 0.8,
                    "keypoint_confidence": 0.7, "raise_margin_ratio": 0.1,
                    "max_gap_seconds": 0.5, "cooldown_seconds": 10})
    detector = HelpGestureDetector(config)
    assert not feed(detector, [0, 0.5, 1, 1.5, 2, 2.5, 3], make_pose(50, 50))
    assert detector.update("person-1", make_pose(left_y=50, right_y=50), 3.5)


@pytest.mark.parametrize("data", [
    {"min_frames": 2.5}, {"min_frames": True}, {"hold_seconds": "1"},
    {"keypoint_confidence": False}, {"unknown": 1},
])
def test_existing_update_rejects_invalid_types_and_unknown_fields(data):
    with pytest.raises(ValueError):
        update(HelpConfig(), data)


@pytest.mark.parametrize("field", [f.name for f in fields(HelpConfig) if f.name != "require_facing_camera"])
@pytest.mark.parametrize("bad", [0, -1, np.nan, np.inf, -np.inf, True, "1"])
def test_detector_rejects_invalid_positive_config(field, bad):
    with pytest.raises(ValueError):
        HelpGestureDetector(HelpConfig(**{field: bad}))


@pytest.mark.parametrize("data", [
    {"min_frames": 2.5}, {"confidence": 1.01},
    {"keypoint_confidence": 1.01}, {"raise_margin_ratio": 1.01},
])
def test_detector_validates_direct_config_construction(data):
    # Direct construction can bypass config.update's integer type checks.
    with pytest.raises(ValueError):
        HelpGestureDetector(HelpConfig(**data))


def test_signal_at_hold_boundary_is_explicitly_only_a_candidate(config):
    detector = HelpGestureDetector(config)
    pose = make_pose()
    before = pose.keypoints.copy()
    assert not feed(detector, [0, 0.25], pose)
    signal = detector.update("person-1", pose, 0.5)
    assert isinstance(signal, Signal)
    assert signal.event_type == "help_requested"
    assert signal.track_id == "person-1"
    assert signal.confidence == pytest.approx(0.9)
    assert signal.details["frames"] == 3
    assert signal.details["hold_seconds"] == 0.5
    assert signal.details["algorithm"] == "temporal_two_hands_raised_extended_v2"
    assert signal.details["gesture"] == "both_hands_raised_extended_away_from_head_candidate"
    assert signal.details["min_elbow_angle_degrees"] == 150
    assert signal.details["min_arm_segment_ratio"] == 0.25
    assert signal.details["min_head_distance_ratio"] == 1
    assert signal.details["arm_order"] == "wrist_above_elbow_above_shoulder_on_each_side"
    assert signal.details["body_scale_reference"] == "max_shoulder_span_and_nose_to_shoulder_midpoint"
    assert "nose_centered" in signal.details["head_reference"]
    assert "cannot infer" in signal.details["limitation"]
    assert "sound" in signal.details["limitation"]
    assert "not_calibrated_event_probability" in signal.details["confidence_kind"]
    json.dumps(signal.details, allow_nan=False)
    np.testing.assert_array_equal(pose.keypoints, before)


def test_many_frames_cannot_replace_required_duration(config):
    detector = HelpGestureDetector(replace(config, hold_seconds=1.0))
    assert not feed(detector, [0, 0.125, 0.25, 0.5, 0.75])
    assert detector.update("person-1", make_pose(), 1.0)


def test_elapsed_time_cannot_replace_required_frame_count(config):
    detector = HelpGestureDetector(replace(config, min_frames=5))
    assert not feed(detector, [0, 0.25, 0.5, 0.625])
    signal = detector.update("person-1", make_pose(), 0.75)
    assert signal.details["frames"] == 5


@pytest.mark.parametrize("left_y,right_y", [
    (300, 300), (60, 300), (300, 60), (120, 120), (80, 80),
])
def test_normal_pose_one_hand_and_below_head_or_margin_do_not_alert(
        config, left_y, right_y):
    detector = HelpGestureDetector(config)
    assert not feed(detector, [i / 4 for i in range(20)],
                    make_pose(left_y, right_y))


def test_just_above_margin_is_accepted(config):
    assert len(feed(HelpGestureDetector(config), [0, 0.25, 0.5],
                    make_pose(79.99, 79.99))) == 1


@pytest.mark.parametrize("shoulder", [5, 6])
def test_each_shoulder_also_bounds_the_reference(config, shoulder):
    pose = make_pose()
    pose.keypoints[shoulder, 1] = 70
    assert not feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)


@pytest.mark.parametrize("scale,dx,dy", [(0.25, 10, -200), (3, -500, 400)])
def test_geometry_is_translation_and_scale_invariant(config, scale, dx, dy):
    pose = make_pose()
    pose.keypoints[:, :2] = pose.keypoints[:, :2] * scale + (dx, dy)
    x1, y1, x2, y2 = pose.bbox
    pose.bbox = (x1 * scale + dx, y1 * scale + dy,
                 x2 * scale + dx, y2 * scale + dy)
    assert len(feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)) == 1


@pytest.mark.parametrize("index", [0, 5, 6, 7, 8, 9, 10])
def test_each_required_keypoint_must_be_confident_and_breaks_hold(config, index):
    detector = HelpGestureDetector(config)
    assert not feed(detector, [0, 0.25])
    pose = make_pose()
    pose.keypoints[index, 2] = config.keypoint_confidence - 0.01
    assert detector.update("person-1", pose, 0.5) is None
    assert not feed(detector, [0.75, 1])
    assert detector.update("person-1", make_pose(), 1.25)


def test_exact_confidence_thresholds_and_low_unused_points(config):
    pose = make_pose()
    pose.confidence = config.confidence
    pose.keypoints[:, 2] = 0
    pose.keypoints[[0, 5, 6, 7, 8, 9, 10], 2] = config.keypoint_confidence
    signal, = feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)
    assert signal.confidence == config.keypoint_confidence


def test_signal_uses_lowest_score_across_the_entire_hold(config):
    detector = HelpGestureDetector(config)
    assert detector.update("person-1", replace(make_pose(), confidence=0.65), 0) is None
    assert detector.update("person-1", make_pose(), 0.25) is None
    assert detector.update("person-1", make_pose(), 0.5).confidence == 0.65


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("part", ["x", "y", "keypoint_score", "unused_point", "pose_score", "bbox", "time"])
def test_nonfinite_inputs_break_hold_and_recover(config, bad, part):
    detector = HelpGestureDetector(config)
    assert not feed(detector, [0, 0.25])
    pose = make_pose()
    t = 0.5
    if part == "time":
        t = bad
    elif part == "bbox":
        pose.bbox = (0, 0, bad, 400)
    elif part == "pose_score":
        pose.confidence = bad
    else:
        index, column = {"x": (9, 0), "y": (10, 1),
                         "keypoint_score": (5, 2), "unused_point": (15, 0)}[part]
        pose.keypoints[index, column] = bad
    assert detector.update("person-1", pose, t) is None
    assert not feed(detector, [0.75, 1])
    assert detector.update("person-1", make_pose(), 1.25)


@pytest.mark.parametrize("kind", ["shape", "columns", "nonnumeric", "bbox", "low_pose_score", "high_pose_score", "high_keypoint_score"])
def test_other_invalid_inputs_break_hold(config, kind):
    detector = HelpGestureDetector(config)
    assert not feed(detector, [0, 0.25])
    pose = make_pose()
    if kind == "shape":
        pose.keypoints = np.zeros((16, 3))
    elif kind == "columns":
        pose.keypoints = np.zeros((17, 2))
    elif kind == "nonnumeric":
        pose.keypoints = [["bad"] * 3] * 17
    elif kind == "bbox":
        pose.bbox = (0, 0, 200, 0)
    elif kind == "low_pose_score":
        pose.confidence = config.confidence - 0.01
    elif kind == "high_pose_score":
        pose.confidence = 1.01
    else:
        pose.keypoints[9, 2] = 1.01
    assert detector.update("person-1", pose, 0.5) is None
    assert not feed(detector, [0.75, 1])
    assert detector.update("person-1", make_pose(), 1.25)


def test_lowering_one_hand_breaks_an_existing_candidate(config):
    detector = HelpGestureDetector(config)
    assert not feed(detector, [0, 0.25])
    assert detector.update("person-1", make_pose(right_y=300), 0.5) is None
    assert not feed(detector, [0.75, 1])
    assert detector.update("person-1", make_pose(), 1.25)


@pytest.mark.parametrize("restart", [0.25, 0.125, 0.75])
def test_duplicate_backward_time_and_excess_gap_restart_candidate(config, restart):
    detector = HelpGestureDetector(config)
    assert not feed(detector, [0, 0.25])
    assert not feed(detector, [restart, restart + 0.25])
    assert detector.update("person-1", make_pose(), restart + 0.5)


def test_same_id_must_supply_both_hands_and_tracks_do_not_share_time(config):
    detector = HelpGestureDetector(config)
    for t in (0, 0.25, 0.5, 0.75):
        assert detector.update("a", make_pose(right_y=300), t) is None
        assert detector.update("b", make_pose(left_y=300), t) is None
    assert detector.update("a", make_pose(), 1) is None
    assert detector.update("a", make_pose(), 1.25) is None
    assert detector.update("b", make_pose(), 1.25) is None
    assert detector.update("a", make_pose(), 1.5).track_id == "a"
    assert detector.update("b", make_pose(), 1.5) is None
    assert detector.update("b", make_pose(), 1.75).track_id == "b"


def test_retain_removes_missing_people_and_preserves_present_candidates(config):
    detector = HelpGestureDetector(config)
    for track in ("a", "b"):
        assert not feed(detector, [0, 0.25], track=track)
    detector.retain({"b"})
    assert set(detector.states) == {"b"}
    assert detector.update("b", make_pose(), 0.5)
    assert not feed(detector, [0.5, 0.75], track="a")
    assert detector.update("a", make_pose(), 1)
    detector.retain(set())
    assert not detector.states
    assert not feed(detector, [1.25, 1.5], track="a")
    assert detector.update("a", make_pose(), 1.75)


def test_continuous_hold_respects_cooldown_including_exact_boundary(config):
    detector = HelpGestureDetector(config)
    assert len(feed(detector, [0, 0.25, 0.5])) == 1
    assert not feed(detector, [i / 4 for i in range(3, 10)])
    assert detector.update("person-1", make_pose(), 2.5)
    # Another ID has an independent cooldown.
    assert len(feed(detector, [2.5, 2.75, 3], track="person-2")) == 1


@pytest.mark.parametrize("break_kind", ["lowered", "head_contact", "bent_arm", "low_confidence", "invalid", "time", "gap", "backward", "duplicate"])
def test_candidate_breaks_do_not_bypass_existing_cooldown(config, break_kind):
    detector = HelpGestureDetector(config)
    assert len(feed(detector, [0, 0.25, 0.5])) == 1
    pose = make_pose()
    if break_kind == "lowered":
        pose = make_pose(right_y=300)
    elif break_kind == "head_contact":
        pose = non_help_pose("touch_head", "left")
    elif break_kind == "bent_arm":
        pose = non_help_pose("bent_away", "right")
    elif break_kind == "low_confidence":
        pose.keypoints[9, 2] = 0.1
    elif break_kind == "invalid":
        pose.keypoints[0, 0] = np.nan
    t = {"time": np.nan, "backward": 0.25, "duplicate": 0.5}.get(break_kind, 0.75)
    if break_kind != "gap":
        assert detector.update("person-1", pose, t) is None
    assert not feed(detector, [i / 4 for i in range(4, 10)])
    assert detector.update("person-1", make_pose(), 2.5)


def test_cooldown_expiry_still_requires_a_new_complete_hold_after_gap(config):
    detector = HelpGestureDetector(config)
    assert len(feed(detector, [0, 0.25, 0.5])) == 1
    assert not feed(detector, [10, 10.25])
    assert detector.update("person-1", make_pose(), 10.5)


def test_reset_clears_pending_holds_and_previous_cooldowns(config):
    detector = HelpGestureDetector(config)
    assert not feed(detector, [0, 0.25])
    detector.reset()
    assert not detector.states
    assert not feed(detector, [0.5, 0.75])
    assert detector.update("person-1", make_pose(), 1)
    detector.reset()
    assert not detector.states
    # A reset starts a new stream, whose time may begin at zero.
    assert len(feed(detector, [0, 0.25, 0.5])) == 1


def non_help_pose(kind, side="both"):
    pose = make_pose()
    kp = pose.keypoints
    for shoulder, elbow, wrist in ((5, 7, 9), (6, 8, 10)):
        if side == "left" and shoulder == 6 or side == "right" and shoulder == 5:
            continue
        if kind == "hold_head":
            kp[elbow, :2] = (25 if shoulder == 5 else 175, 85)
            kp[wrist, :2] = (90 if shoulder == 5 else 110, 50)
        elif kind == "touch_head":
            # Even a straight arm reaching the crown stays inside the head zone.
            kp[wrist, :2] = (100, 60)
            kp[elbow, :2] = (kp[shoulder, :2] + kp[wrist, :2]) / 2
        elif kind == "bent_away":
            # Raised wrists well away from the head, but elbows are folded.
            kp[elbow, :2] = (-30 if shoulder == 5 else 230, 80)
        elif kind == "elbow_above_wrist":
            kp[elbow, 1] = 20
        elif kind == "zero_upper_arm":
            kp[elbow, :2] = kp[shoulder, :2]
        elif kind == "zero_forearm":
            kp[elbow, :2] = kp[wrist, :2]
        elif kind == "tiny_upper_arm":
            kp[elbow, :2] = kp[shoulder, :2] * 0.99 + kp[wrist, :2] * 0.01
        elif kind == "tiny_forearm":
            kp[elbow, :2] = kp[shoulder, :2] * 0.01 + kp[wrist, :2] * 0.99
        else:
            raise AssertionError(kind)
    return pose


@pytest.mark.parametrize("side", ["left", "right", "both"])
@pytest.mark.parametrize("kind", ["hold_head", "touch_head", "bent_away",
                                  "elbow_above_wrist", "zero_upper_arm",
                                  "zero_forearm", "tiny_upper_arm", "tiny_forearm"])
def test_head_contact_bent_and_degenerate_arms_rejected_and_break_hold(config, kind, side):
    pose = non_help_pose(kind, side)
    # These wrists pass v1's sole height test: nose=100, bbox margin=20.
    assert (pose.keypoints[[9, 10], 1] < 80).all()
    detector = HelpGestureDetector(config)
    assert not feed(detector, [i / 4 for i in range(12)], pose)
    assert not feed(detector, [3, 3.25])
    assert detector.update("person-1", pose, 3.5) is None
    assert not feed(detector, [3.75, 4])
    assert detector.update("person-1", make_pose(), 4.25)


@pytest.mark.parametrize("wrist_y,accepted", [(20, False), (19.99, True)])
def test_head_exclusion_radius_boundary_with_straight_arms(config, wrist_y, accepted):
    pose = make_pose()
    pose.keypoints[[9, 10], :2] = (100, wrist_y)
    pose.keypoints[[7, 8], :2] = (pose.keypoints[[5, 6], :2]
                                 + pose.keypoints[[9, 10], :2]) / 2
    assert bool(feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)) == accepted


@pytest.mark.parametrize("angle,accepted", [(149.9, False), (150.1, True), (170, True)])
@pytest.mark.parametrize("side", [0, 1])
def test_each_elbow_must_be_nearly_straight(config, angle, accepted, side):
    pose = make_pose()
    shoulder, elbow, wrist = (5 + side, 7 + side, 9 + side)
    upper = np.array([-20.0 if side == 0 else 20.0, -60.0])
    bend = np.radians(180 - angle) * (1 if side == 0 else -1)
    rotation = np.array([[np.cos(bend), -np.sin(bend)],
                         [np.sin(bend), np.cos(bend)]])
    pose.keypoints[elbow, :2] = pose.keypoints[shoulder, :2] + upper
    pose.keypoints[wrist, :2] = pose.keypoints[elbow, :2] + rotation @ upper
    assert bool(feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)) == accepted


def test_narrow_shoulders_still_use_nose_to_shoulder_distance(config):
    pose = make_pose()
    pose.keypoints[[5, 6], 0] = (95, 105)
    pose.keypoints[[9, 10], :2] = ((98, 60), (102, 60))
    pose.keypoints[[7, 8], :2] = (pose.keypoints[[5, 6], :2]
                                 + pose.keypoints[[9, 10], :2]) / 2
    assert not feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)
    pose.keypoints[[9, 10], 1] = 20
    pose.keypoints[[7, 8], :2] = (pose.keypoints[[5, 6], :2]
                                 + pose.keypoints[[9, 10], :2]) / 2
    assert len(feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)) == 1


def test_zero_upper_body_scale_is_rejected(config):
    pose = make_pose()
    pose.keypoints[[0, 5, 6], :2] = (100, 150)
    assert not feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)


@pytest.mark.parametrize("elbow", [7, 8])
def test_elbow_score_contributes_to_confidence_over_hold(config, elbow):
    detector = HelpGestureDetector(config)
    pose = make_pose()
    pose.keypoints[elbow, 2] = 0.6
    assert detector.update("person-1", pose, 0) is None
    assert detector.update("person-1", make_pose(), 0.25) is None
    assert detector.update("person-1", make_pose(), 0.5).confidence == 0.6


@pytest.mark.parametrize("scale,dx,dy", [(0.25, 10, -200), (3, -500, 400)])
@pytest.mark.parametrize("kind", ["hold_head", "touch_head", "bent_away"])
def test_non_help_geometry_remains_rejected_after_scale_and_translation(config, scale, dx, dy, kind):
    pose = non_help_pose(kind)
    pose.keypoints[:, :2] = pose.keypoints[:, :2] * scale + (dx, dy)
    x1, y1, x2, y2 = pose.bbox
    pose.bbox = (x1 * scale + dx, y1 * scale + dy,
                 x2 * scale + dx, y2 * scale + dy)
    assert not feed(HelpGestureDetector(config), [0, 0.25, 0.5], pose)

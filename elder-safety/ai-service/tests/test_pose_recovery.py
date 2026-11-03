from __future__ import annotations

from dataclasses import replace
import math
from unittest.mock import Mock

import numpy as np
import pytest

from ai_service.pose_recovery import needs_recovery, recover_poses
from ai_service.types import Pose

HEIGHT, WIDTH = 120, 240


def make_pose(*, angle=0.0, score=0.9, core=0.9, feet=0.9, bbox=None):
    points = np.zeros((17, 3), dtype=float)
    points[:, :2] = (80, 30)
    points[:, 2] = 0.8
    shoulder = np.array([80.0, 30.0])
    hip = shoulder + 40 * np.array([math.sin(math.radians(angle)), math.cos(math.radians(angle))])
    points[5, :2], points[6, :2] = shoulder + (-3, 0), shoulder + (3, 0)
    points[11, :2], points[12, :2] = hip + (-3, 0), hip + (3, 0)
    points[[5, 6, 11, 12], 2] = core
    points[15, :2], points[16, :2] = (150, 50), (160, 60)
    points[[15, 16], 2] = feet
    return Pose(bbox or (50, 5, 110, 115), points, score)


def rotated_pose(pose, clockwise):
    """Encode an original pose as a predictor would see it after rotation."""
    points = pose.keypoints.copy()
    if clockwise:
        points[:, 0] = HEIGHT - 1 - pose.keypoints[:, 1]
        points[:, 1] = pose.keypoints[:, 0]
    else:
        points[:, 0] = pose.keypoints[:, 1]
        points[:, 1] = WIDTH - 1 - pose.keypoints[:, 0]
    x1, y1, x2, y2 = pose.bbox
    box = ((HEIGHT - y2, x1, HEIGHT - y1, x2) if clockwise
           else (y1, WIDTH - x2, y2, WIDTH - x1))
    return replace(pose, bbox=box, keypoints=points)


@pytest.fixture
def image():
    return np.arange(HEIGHT * WIDTH * 3, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)


def test_reliable_standing_skips_predict_and_preserves_object(image):
    base = make_pose()
    predict = Mock(side_effect=AssertionError("unexpected inference"))
    assert not needs_recovery([base])
    result = recover_poses(image, [base], predict)
    assert len(result) == 1 and result[0] is base
    predict.assert_not_called()


@pytest.mark.parametrize("poses,expected_calls", [
    ([], 3), ([make_pose(core=0.549)], 3), ([make_pose(angle=35)], 2),
    ([make_pose(bbox=(30, 10, 105, 110))], 2),
    ([make_pose(), make_pose(core=0.1)], 2),
    ([replace(make_pose(), keypoints=np.zeros((16, 3)))], 3),
])
def test_recovery_triggers_bounded_calls(image, poses, expected_calls):
    assert needs_recovery(poses)
    predict = Mock(return_value=[])
    recover_poses(image, poses, predict)
    assert predict.call_count == expected_calls
    np.testing.assert_array_equal(predict.call_args_list[0].args[0], np.rot90(image, -1))
    np.testing.assert_array_equal(predict.call_args_list[1].args[0], np.rot90(image, 1))


@pytest.mark.parametrize("pose", [
    make_pose(core=0.55, angle=34.9, bbox=(30, 10, 104.9, 110)),
    make_pose(score=0.1, feet=0.1),
])
def test_trigger_thresholds_do_not_add_feet_or_score_gate(pose):
    assert not needs_recovery([pose])


@pytest.mark.parametrize("clockwise", [True, False])
def test_non_square_mapping_all_keypoints_box_corners_and_semantics(image, clockwise):
    original = make_pose(angle=90, bbox=(20, 10, 220, 100))
    original.keypoints[0, :2] = (0, 0)
    original.keypoints[1, :2] = (WIDTH - 1, HEIGHT - 1)
    original.keypoints[2, :2] = (0, HEIGHT - 1)
    original.keypoints[3, :2] = (WIDTH - 1, 0)
    original.keypoints[:, 2] = np.linspace(0.6, 0.95, 17)
    predicted = rotated_pose(original, clockwise)
    saved = predicted.keypoints.copy()
    outputs = [[predicted], []] if clockwise else [[], [predicted]]
    predict = Mock(side_effect=outputs)
    result = recover_poses(image, [], predict)
    assert len(result) == 1
    assert result[0].bbox == pytest.approx((20, 10, 220, 100))
    np.testing.assert_allclose(result[0].keypoints, original.keypoints, atol=1e-12)
    np.testing.assert_array_equal(predicted.keypoints, saved)
    assert not np.shares_memory(result[0].keypoints, predicted.keypoints)
    assert result[0].confidence == original.confidence
    assert all(call.args[0].shape == (WIDTH, HEIGHT, 3) for call in predict.call_args_list)


@pytest.mark.parametrize("clockwise", [True, False])
def test_clips_boxes_and_recovered_points_without_modifying_input(image, clockwise):
    original = make_pose(angle=90, bbox=(-15, -10, WIDTH + 5, HEIGHT + 10))
    original.keypoints[0, :2] = (-5, HEIGHT + 8)
    predicted = rotated_pose(original, clockwise)
    saved_points, saved_box = predicted.keypoints.copy(), predicted.bbox
    predict = Mock(side_effect=[[predicted], []] if clockwise else [[], [predicted]])
    result = recover_poses(image, [], predict)
    assert len(result) == 1
    assert result[0].bbox == (0, 0, WIDTH, HEIGHT)
    np.testing.assert_array_equal(result[0].keypoints[0, :2], [0, HEIGHT - 1])
    np.testing.assert_array_equal(predicted.keypoints, saved_points)
    assert predicted.bbox == saved_box


@pytest.mark.parametrize("field,value", [
    ("score", 0.349), ("core", 0.549), ("feet", 0.549), ("angle", 39.9), ("angle", 0),
])
def test_rejects_low_confidence_and_original_frame_standing(image, field, value):
    kwargs = dict(angle=90, bbox=(20, 10, 210, 100))
    kwargs[field] = value
    original = make_pose(**kwargs)
    predict = Mock(side_effect=[[rotated_pose(original, True)], [rotated_pose(original, False)], []])
    assert recover_poses(image, [], predict) == []
    assert predict.call_count == 3


@pytest.mark.parametrize("index", [5, 6, 11, 12, 15, 16])
def test_every_core_and_foot_point_must_pass(image, index):
    original = make_pose(angle=90)
    original.keypoints[index, 2] = 0.54
    assert recover_poses(image, [], Mock(side_effect=[[rotated_pose(original, True)], [], []])) == []


def test_acceptance_thresholds_are_inclusive(image):
    original = make_pose(angle=40, score=0.35, core=0.55, feet=0.55)
    result = recover_poses(image, [], Mock(side_effect=[[rotated_pose(original, True)], []]))
    assert len(result) == 1
    assert result[0].confidence == 0.35


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("target", ["point", "bbox", "score"])
def test_rejects_nonfinite_data_anywhere(image, bad, target):
    pose = rotated_pose(make_pose(angle=90), True)
    if target == "point":
        pose.keypoints[0, 0] = bad
    elif target == "bbox":
        pose.bbox = (bad, 10, 100, 200)
    else:
        pose.confidence = bad
    assert needs_recovery([pose])
    assert recover_poses(image, [pose], Mock(return_value=[pose])) == []


@pytest.mark.parametrize("shape", [(16, 3), (17, 2), (17, 4), (51,), (1, 17, 3)])
def test_rejects_wrong_keypoint_shape(image, shape):
    pose = replace(make_pose(), keypoints=np.ones(shape))
    assert recover_poses(image, [], Mock(return_value=[pose])) == []


@pytest.mark.parametrize("box", [(10, 10, 10, 30), (20, 10, 5, 30), (1, 2, 3),
                                      [[1, 2, 3, 4]], (130, 10, 150, 50)])
def test_rejects_invalid_or_fully_outside_rotated_boxes(image, box):
    pose = replace(rotated_pose(make_pose(angle=90), True), bbox=box)
    assert recover_poses(image, [], Mock(side_effect=[[pose], [], []])) == []


@pytest.mark.parametrize("bad", [-0.1, 1.1, "bad"])
def test_rejects_invalid_scores(image, bad):
    pose = replace(make_pose(angle=90), confidence=bad)
    assert recover_poses(image, [], Mock(return_value=[pose])) == []


def test_undefined_torso_triggers_but_is_not_recovered(image):
    pose = make_pose()
    pose.keypoints[[11, 12], :2] = pose.keypoints[[5, 6], :2]
    assert needs_recovery([pose])
    assert recover_poses(image, [], Mock(side_effect=[[rotated_pose(pose, True)], [], []])) == []


def test_nms_replaces_weaker_base_keeps_separate_base_and_removes_rotation_duplicates(image):
    weak = make_pose(angle=90, score=0.95, core=0.6, bbox=(20, 10, 160, 100))
    separate = make_pose(bbox=(190, 5, 230, 115))
    better = make_pose(angle=90, score=0.8, core=0.95, bbox=weak.bbox)
    cw, ccw = rotated_pose(better, True), rotated_pose(better, False)
    result = recover_poses(image, [weak, separate], Mock(side_effect=[[cw], [ccw]]))
    assert len(result) == 2
    assert result[0] is separate
    assert result[1].confidence == better.confidence
    assert result[1].bbox == weak.bbox
    assert all(pose is not weak for pose in result)


def test_higher_quality_base_wins_and_ties_prefer_base(image):
    base = make_pose(angle=90, core=0.95, score=0.9)
    lower = make_pose(angle=90, core=0.6, score=1.0)
    result = recover_poses(image, [base], Mock(side_effect=[
        [rotated_pose(lower, True)], [rotated_pose(base, False)]]))
    assert len(result) == 1 and result[0] is base


def test_base_duplicates_are_suppressed_even_without_recovery(image):
    weaker = make_pose(core=0.6, score=0.99)
    better = make_pose(core=0.95, score=0.8)
    predict = Mock(side_effect=AssertionError("standing should skip inference"))
    result = recover_poses(image, [weaker, better, better], predict)
    assert len(result) == 1 and result[0] is better
    predict.assert_not_called()


@pytest.mark.parametrize("offset,count", [(7, 1), (7.01, 2)])
def test_nms_iou_point_three_is_inclusive(image, offset, count):
    first = make_pose(bbox=(0, 0, 13, 100))
    second = make_pose(bbox=(offset, 0, offset + 13, 100), score=0.8)
    result = recover_poses(image, [first, second], Mock(return_value=[]))
    assert len(result) == count
    assert result[0] is first


def test_inputs_unchanged_even_when_predict_writes_rotated_image(image):
    base = make_pose(core=0.1, bbox=(-10, 5, 110, 115))
    base.keypoints = base.keypoints.astype(np.float32)
    predicted = rotated_pose(make_pose(angle=90), True)
    before_image, before_base, before_extra = image.copy(), base.keypoints.copy(), predicted.keypoints.copy()
    poses = [base]
    calls = []

    def predict(rotated):
        calls.append(rotated.shape)
        rotated[:] = 0
        return [predicted] if len(calls) == 1 else []

    result = recover_poses(image, poses, predict)
    assert len(result) == 1
    assert poses == [base]
    assert base.bbox == (-10, 5, 110, 115)
    np.testing.assert_array_equal(image, before_image)
    np.testing.assert_array_equal(base.keypoints, before_base)
    np.testing.assert_array_equal(predicted.keypoints, before_extra)
    assert base.keypoints.dtype == np.float32


def test_clipping_base_preserves_keypoint_array_and_low_confidence(image):
    base = make_pose(core=0.1, bbox=(-10, 5, 110, 115))
    result = recover_poses(image, [base], Mock(return_value=[]))
    assert len(result) == 1
    assert result[0].bbox == (0, 5, 110, 115)
    assert result[0].keypoints is base.keypoints
    assert base.bbox == (-10, 5, 110, 115)


@pytest.mark.parametrize("bad_image", [None, np.zeros((0, 10, 3)), np.zeros((10, 0)),
                                            np.zeros((10,)), np.zeros((10, 10, 0))])
def test_rejects_empty_or_invalid_image_without_predict(bad_image):
    predict = Mock()
    with pytest.raises(ValueError, match="image"):
        recover_poses(bad_image, [], predict)
    predict.assert_not_called()


def test_predict_errors_are_not_hidden(image):
    with pytest.raises(RuntimeError, match="inference failed"):
        recover_poses(image, [], Mock(side_effect=RuntimeError("inference failed")))


def half_turn_pose(pose):
    points = pose.keypoints.copy()
    points[:, 0] = WIDTH - 1 - pose.keypoints[:, 0]
    points[:, 1] = HEIGHT - 1 - pose.keypoints[:, 1]
    x1, y1, x2, y2 = pose.bbox
    return replace(pose, bbox=(WIDTH - x2, HEIGHT - y2, WIDTH - x1, HEIGHT - y1),
                   keypoints=points)


def inverted_pose(*, score=0.9, core=0.9, feet=0.9):
    pose = make_pose(score=score, core=core, feet=feet, bbox=(40, 5, 180, 115))
    pose.keypoints[[5, 6], :2] = [(77, 100), (83, 100)]
    pose.keypoints[[11, 12], :2] = [(77, 65), (83, 65)]
    pose.keypoints[[15, 16], :2] = [(77, 20), (83, 30)]
    return pose


def test_half_turn_non_square_mapping_and_no_input_mutation(image):
    original = inverted_pose()
    original.keypoints[:4, :2] = [(0, 0), (WIDTH - 1, HEIGHT - 1),
                                 (0, HEIGHT - 1), (WIDTH - 1, 0)]
    original.keypoints[:, 2] = np.linspace(0.6, 0.95, 17)
    predicted = half_turn_pose(original)
    saved_points, saved_box, saved_image = predicted.keypoints.copy(), predicted.bbox, image.copy()
    predict = Mock(side_effect=[[], [], [predicted]])
    result = recover_poses(image, [], predict)
    assert predict.call_count == 3
    np.testing.assert_array_equal(predict.call_args_list[2].args[0], np.rot90(image, 2))
    assert predict.call_args_list[2].args[0].shape == image.shape
    assert len(result) == 1
    assert result[0].bbox == pytest.approx((40, 5, 180, 115))
    np.testing.assert_allclose(result[0].keypoints, original.keypoints)
    np.testing.assert_array_equal(predicted.keypoints, saved_points)
    np.testing.assert_array_equal(image, saved_image)
    assert predicted.bbox == saved_box
    assert not np.shares_memory(result[0].keypoints, predicted.keypoints)


@pytest.mark.parametrize("clockwise", [True, False])
def test_inverted_quarter_candidate_prevents_half_turn(image, clockwise):
    original = inverted_pose(score=0.35, core=0.55, feet=0.55)
    predicted = rotated_pose(original, clockwise)
    predict = Mock(side_effect=[[predicted], []] if clockwise else [[], [predicted]])
    result = recover_poses(image, [], predict)
    assert predict.call_count == 2
    assert len(result) == 1
    np.testing.assert_allclose(result[0].keypoints, original.keypoints)


@pytest.mark.parametrize("base", [
    make_pose(angle=90),
    make_pose(angle=90, feet=0.1, score=0.1),
    make_pose(core=0.55, bbox=(20, 5, 200, 115)),
])
def test_reliable_base_core_prevents_half_turn_even_without_good_feet_or_score(image, base):
    predict = Mock(side_effect=[[], []])
    result = recover_poses(image, [base], predict)
    assert needs_recovery([base])
    assert predict.call_count == 2
    assert len(result) == 1 and result[0] is base


@pytest.mark.parametrize("clockwise", [True, False])
def test_accepted_horizontal_quarter_prevents_half_turn_for_weak_base(image, clockwise):
    base = make_pose(core=0.1)
    original = make_pose(angle=90)
    predicted = rotated_pose(original, clockwise)
    predict = Mock(side_effect=[[predicted], []] if clockwise else [[], [predicted]])
    result = recover_poses(image, [base], predict)
    assert predict.call_count == 2
    assert len(result) == 1 and result[0].confidence == original.confidence


@pytest.mark.parametrize("bad_kind", ["standing", "low_core", "low_feet", "low_score", "nan", "outside"])
def test_rejected_quarter_outputs_do_not_block_half_turn(image, bad_kind):
    original = inverted_pose()
    if bad_kind == "standing":
        original = make_pose()
    elif bad_kind == "low_core":
        original.keypoints[5, 2] = 0.54
    elif bad_kind == "low_feet":
        original.keypoints[16, 2] = 0.54
    elif bad_kind == "low_score":
        original.confidence = 0.34
    elif bad_kind == "nan":
        original.keypoints[0, 0] = np.nan
    else:
        original.bbox = (WIDTH + 5, 0, WIDTH + 50, HEIGHT)
    expected = inverted_pose()
    predict = Mock(side_effect=[[rotated_pose(original, True)],
                               [rotated_pose(original, False)], [half_turn_pose(expected)]])
    result = recover_poses(image, [], predict)
    assert predict.call_count == 3
    assert len(result) == 1
    np.testing.assert_allclose(result[0].keypoints, expected.keypoints)


@pytest.mark.parametrize("index", [5, 6, 11, 12, 15, 16])
@pytest.mark.parametrize("rotation", ["cw", "ccw", "half"])
def test_inverted_candidates_still_require_each_core_and_foot(image, index, rotation):
    original = inverted_pose()
    original.keypoints[index, 2] = 0.549
    predictions = [[], [], []]
    if rotation == "half":
        predictions[2] = [half_turn_pose(original)]
    else:
        clockwise = rotation == "cw"
        predictions[0 if clockwise else 1] = [rotated_pose(original, clockwise)]
    predict = Mock(side_effect=predictions)
    assert recover_poses(image, [], predict) == []
    assert predict.call_count == 3


@pytest.mark.parametrize("box_height,shoulder_gap,ankle_gap,accepted", [
    (110, 11, 40, False), (110, 11.01, 40, True),
    (40, 5, 40, False), (40, 5.01, 40, True),
    (110, 35, 5, False), (110, 35, 5.01, True),
    (110, -35, 40, False), (110, 35, -5, False),
])
def test_inverted_ordering_uses_strict_pixel_and_box_relative_margins(
    image, box_height, shoulder_gap, ankle_gap, accepted,
):
    original = inverted_pose()
    original.bbox = (40, 5, 180, 5 + box_height)
    original.keypoints[[5, 6], 1] = 65 + shoulder_gap
    original.keypoints[[15, 16], 1] = 65 - ankle_gap
    predict = Mock(side_effect=[[], [], [half_turn_pose(original)]])
    result = recover_poses(image, [], predict)
    assert len(result) == int(accepted)
    assert predict.call_count == 3


@pytest.mark.parametrize("original", [
    inverted_pose(score=0.349), make_pose(),
    replace(inverted_pose(), keypoints=np.ones((16, 3))),
    replace(inverted_pose(), confidence=np.nan),
])
def test_half_turn_rejects_unreliable_or_standing_candidates(image, original):
    predict = Mock(side_effect=[[], [], [half_turn_pose(original)]])
    assert recover_poses(image, [], predict) == []
    assert predict.call_count == 3


def test_half_turn_accepts_horizontal_and_exact_confidence_thresholds(image):
    original = make_pose(angle=90, score=0.35, core=0.55, feet=0.55)
    result = recover_poses(image, [], Mock(side_effect=[[], [], [half_turn_pose(original)]]))
    assert len(result) == 1
    np.testing.assert_allclose(result[0].keypoints, original.keypoints)


def test_half_turn_inverted_threshold_scores_are_inclusive(image):
    original = inverted_pose(score=0.35, core=0.55, feet=0.55)
    result = recover_poses(image, [], Mock(side_effect=[[], [], [half_turn_pose(original)]]))
    assert len(result) == 1
    np.testing.assert_allclose(result[0].keypoints, original.keypoints)


def test_degenerate_base_core_does_not_block_half_turn(image):
    base = make_pose()
    base.keypoints[[11, 12], :2] = base.keypoints[[5, 6], :2]
    predict = Mock(return_value=[])
    result = recover_poses(image, [base], predict)
    assert predict.call_count == 3
    assert len(result) == 1 and result[0] is base


def test_half_turn_clipping_nms_and_input_immutability(image):
    original = inverted_pose()
    original.bbox = (-5, -10, WIDTH + 5, HEIGHT + 10)
    original.keypoints[0, :2] = (-5, HEIGHT + 10)
    predicted = half_turn_pose(original)
    saved = predicted.keypoints.copy()
    weaker = replace(predicted, confidence=0.4)
    base = replace(inverted_pose(core=0.1), bbox=original.bbox)
    image_before = image.copy()
    calls = []

    def predict(rotated):
        calls.append(rotated.shape)
        rotated[:] = 0
        return [weaker, predicted, predicted] if len(calls) == 3 else []

    result = recover_poses(image, [base], predict)
    assert len(calls) == 3 and len(result) == 1
    assert result[0].confidence == original.confidence
    assert result[0].bbox == (0, 0, WIDTH, HEIGHT)
    np.testing.assert_array_equal(result[0].keypoints[0, :2], (0, HEIGHT - 1))
    np.testing.assert_array_equal(predicted.keypoints, saved)
    np.testing.assert_array_equal(image, image_before)
    assert base.bbox == original.bbox


def test_half_turn_nms_preserves_nonoverlapping_weak_base(image):
    base = make_pose(core=0.1, bbox=(190, 5, 230, 115))
    original = inverted_pose()
    result = recover_poses(image, [base], Mock(side_effect=[[], [], [half_turn_pose(original)]]))
    assert len(result) == 2 and result[1] is base


def test_half_turn_is_not_sticky_across_frames(image):
    predict = Mock(side_effect=[[], [], []])
    assert recover_poses(image, [], predict) == []
    standing = make_pose()
    result = recover_poses(image, [standing], predict)
    assert len(result) == 1 and result[0] is standing
    assert predict.call_count == 3

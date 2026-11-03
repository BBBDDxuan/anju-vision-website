"""Optional rotated pose recovery, independent of model loading and resizing.

Keypoints use pixel centres (0 .. width-1); xyxy boxes use continuous image
edges (0 .. width). COCO indices retain their anatomical left/right meaning.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
import math

import cv2
import numpy as np

from .types import Pose, iou

__all__ = ["needs_recovery", "recover_poses"]

_CORE = [5, 6, 11, 12]  # shoulders and hips
_FEET = [15, 16]  # ankles in COCO's 17-keypoint layout


def _valid(pose: Pose) -> bool:
    """Reject malformed geometry/scores before indexing or ranking a pose."""
    try:
        points = np.asarray(pose.keypoints, dtype=float)
        box = np.asarray(pose.bbox, dtype=float)
        score = float(pose.confidence)
        return bool(
            points.shape == (17, 3)
            and box.shape == (4,)
            and np.isfinite(points).all()
            and np.isfinite(box).all()
            and math.isfinite(score)
            and 0 <= score <= 1
            and ((points[:, 2] >= 0) & (points[:, 2] <= 1)).all()
            and box[2] > box[0]
            and box[3] > box[1]
        )
    except (TypeError, ValueError, OverflowError):
        return False


def _angle(pose: Pose) -> float:
    points = np.asarray(pose.keypoints, dtype=float)
    shoulder = points[[5, 6], :2].mean(axis=0)
    hip = points[[11, 12], :2].mean(axis=0)
    dx, dy = hip - shoulder
    if dx == 0 and dy == 0:
        return math.nan
    return math.degrees(math.atan2(abs(dx), abs(dy)))


def _core_confidence(pose: Pose) -> float:
    return float(np.asarray(pose.keypoints)[_CORE, 2].min())


def needs_recovery(poses: Sequence[Pose]) -> bool:
    """Request recovery for missing, unreliable, tilted or wide detections.

    A malformed pose or undefined torso direction is also unreliable. Feet and
    detector scores are acceptance gates for extras, not additional triggers.
    """
    if len(poses) == 0:
        return True
    for pose in poses:
        if not _valid(pose):
            return True
        angle = _angle(pose)
        x1, y1, x2, y2 = pose.bbox
        if (_core_confidence(pose) < 0.55 or not math.isfinite(angle)
                or angle >= 35 or (x2 - x1) / (y2 - y1) >= 0.75):
            return True
    return False


def _clip_box(pose: Pose, width: int, height: int) -> Pose | None:
    box = np.asarray(pose.bbox, dtype=float).copy()
    box[[0, 2]] = np.clip(box[[0, 2]], 0, width)
    box[[1, 3]] = np.clip(box[[1, 3]], 0, height)
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    if np.array_equal(box, pose.bbox):
        return pose
    return replace(pose, bbox=tuple(float(v) for v in box))


def _unrotate(pose: Pose, width: int, height: int, rotation: int) -> Pose:
    points = np.array(pose.keypoints, dtype=float, copy=True)
    x, y = points[:, 0].copy(), points[:, 1].copy()
    if rotation == cv2.ROTATE_90_CLOCKWISE:
        points[:, 0], points[:, 1] = y, height - 1 - x
    elif rotation == cv2.ROTATE_90_COUNTERCLOCKWISE:
        points[:, 0], points[:, 1] = width - 1 - y, x
    else:  # 180 degrees
        points[:, 0], points[:, 1] = width - 1 - x, height - 1 - y
    points[:, 0] = np.clip(points[:, 0], 0, width - 1)
    points[:, 1] = np.clip(points[:, 1], 0, height - 1)

    # Transform all four box corners. Box edges use H/W, unlike pixel centres.
    x1, y1, x2, y2 = pose.bbox
    corners = np.array([[x1, y1], [x2, y1], [x1, y2], [x2, y2]], dtype=float)
    x, y = corners[:, 0].copy(), corners[:, 1].copy()
    if rotation == cv2.ROTATE_90_CLOCKWISE:
        corners[:, 0], corners[:, 1] = y, height - x
    elif rotation == cv2.ROTATE_90_COUNTERCLOCKWISE:
        corners[:, 0], corners[:, 1] = width - y, x
    else:  # 180 degrees
        corners[:, 0], corners[:, 1] = width - x, height - y
    lo, hi = corners.min(axis=0), corners.max(axis=0)
    return replace(pose, bbox=(float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])),
                   keypoints=points)



def _reliable_core(pose: Pose) -> bool:
    # Call only for validated, clipped base poses. Feet and detector confidence
    # do not disqualify an otherwise reliable, nondegenerate core skeleton.
    return _core_confidence(pose) >= 0.55 and math.isfinite(_angle(pose))


def _inverted_full_body(pose: Pose) -> bool:
    """Compare shoulder, hip and ankle pair centres in original-frame pixels."""
    points = np.asarray(pose.keypoints)
    shoulder_y = float(points[[5, 6], 1].mean())
    hip_y = float(points[[11, 12], 1].mean())
    ankle_y = float(points[_FEET, 1].mean())
    box_height = pose.bbox[3] - pose.bbox[1]
    return (shoulder_y > hip_y + max(5.0, 0.1 * box_height)
            and hip_y > ankle_y + 5.0)


def _recovery_candidate(pose: Pose, width: int, height: int, rotation: int) -> Pose | None:
    if not _valid(pose):
        return None
    points = np.asarray(pose.keypoints)
    if (pose.confidence < 0.35 or _core_confidence(pose) < 0.55
            or points[_FEET, 2].min() < 0.55):
        return None
    recovered = _clip_box(_unrotate(pose, width, height, rotation), width, height)
    if recovered is not None and (_angle(recovered) >= 40 or _inverted_full_body(recovered)):
        return recovered
    return None


def recover_poses(
    image: np.ndarray,
    poses: Sequence[Pose],
    predict: Callable[[np.ndarray], list[Pose]],
) -> list[Pose]:
    """Merge base poses with reliable lying or inverted full-body poses.

    ``predict`` receives an unresized rotated image and returns coordinates in
    that image's pixels. The caller owns model loading and any internal resize.
    Recovery first predicts CW and CCW once each. A 180-degree pass is added
    only if neither yields an accepted candidate and base has no reliable core
    (all shoulders/hips >= .55 with a defined torso direction). At most three
    extra passes run, and none run unless ``needs_recovery(poses)`` is true.
    Predictor exceptions propagate; no model is loaded or downloaded here.

    Invalid poses and boxes outside the image are discarded. Recovery candidates
    require score >= .35, all shoulders/hips/ankles >= .55, and an original-frame
    torso angle >= 40 degrees from vertical OR inverted full-body ordering:
    shoulder_y > hip_y + max(5, .1 * clipped_box_height), hip_y > ankle_y + 5.
    These y values are pair centres after mapping and clipping.
    Valid base poses retain their scores
    and keypoints, including low-confidence detections. Both paths perform stable
    score-descending NMS at IoU >= .3; ties prefer base, then CW, CCW, then 180.
    Inputs are never mutated; unchanged base objects may be shared by the result.
    """
    if (not isinstance(image, np.ndarray) or image.ndim not in (2, 3)
            or image.shape[0] == 0 or image.shape[1] == 0
            or (image.ndim == 3 and image.shape[2] == 0)):
        raise ValueError("image must be a nonempty HxW or HxWxC numpy array")
    height, width = image.shape[:2]
    base = list(poses)
    candidates = []
    for pose in base:
        if _valid(pose):
            clipped = _clip_box(pose, width, height)
            if clipped is not None:
                candidates.append(clipped)

    if needs_recovery(base):
        has_reliable_core = any(_reliable_core(pose) for pose in candidates)
        extras = []
        for rotation in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
            # OpenCV allocates independent storage, including for predictor writes.
            rotated = cv2.rotate(image, rotation)
            for pose in predict(rotated):
                recovered = _recovery_candidate(pose, width, height, rotation)
                if recovered is not None:
                    extras.append(recovered)
        if not extras and not has_reliable_core:
            for pose in predict(cv2.rotate(image, cv2.ROTATE_180)):
                recovered = _recovery_candidate(pose, width, height, cv2.ROTATE_180)
                if recovered is not None:
                    extras.append(recovered)
        candidates.extend(extras)

    ranked = sorted(candidates, key=lambda p: _core_confidence(p) * p.confidence, reverse=True)
    kept: list[Pose] = []
    for pose in ranked:
        if not any(iou(pose.bbox, other.bbox) >= 0.3 for other in kept):
            kept.append(pose)
    return kept

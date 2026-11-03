"""Local COCO17 candidate for two raised, extended arms away from the head.

Image y increases downwards. Both wrists must clear the higher of the nose
and shoulders by a bbox-relative margin. Each wrist must be above its elbow,
its elbow above its shoulder, and its elbow angle at least 150 degrees.
Wrists must also lie outside a conservative nose-centered head exclusion
radius: max(shoulder span, nose-to-shoulder-midpoint distance). Required nose,
shoulders, elbows and wrists must be reliable; tiny/degenerate limbs fail.
The 2D nose proxy cannot establish hand/head contact or help-seeking intent,
and this rule cannot detect speech, shouts, or any other sound. Optional
front-facing filtering uses only nose/eye geometry, never iris position or gaze.

Call retain(present_track_ids) every frame, including frames with no people.
Use reset() on a stream/session change; t is monotonic stream time in seconds.
The detection engine integrates this rule; help_requested uses the normal alert queue.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import math

import numpy as np

from .types import Pose, Signal, finite_box


_MIN_ELBOW_ANGLE_DEGREES = 150.0
_MIN_ARM_SEGMENT_RATIO = 0.25
_MIN_HEAD_DISTANCE_RATIO = 1.0
# Heuristic image geometry, not calibrated head-pose angles or gaze estimates.
_MAX_NOSE_EYE_OFFSET_RATIO = 0.35
_MIN_EYE_SHOULDER_RATIO = 0.10
_MAX_EYE_SHOULDER_RATIO = 0.80
_MAX_EYE_LINE_ROLL_DEGREES = 35.0


@dataclass
class HelpConfig:
    confidence: float = 0.60
    keypoint_confidence: float = 0.55
    raise_margin_ratio: float = 0.05  # fraction of bbox height above the reference
    hold_seconds: float = 1.0
    min_frames: int = 5
    max_gap_seconds: float = 0.30
    cooldown_seconds: float = 15.0
    # Default stays compatible; the camera configuration enables this filter.
    require_facing_camera: bool = False

    def validate(self) -> None:
        if not isinstance(self.require_facing_camera, bool):
            raise ValueError("require_facing_camera must be boolean")
        for f in fields(self):
            if f.name == "require_facing_camera":
                continue
            value = getattr(self, f.name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{f.name} must be finite and positive")
        if not isinstance(self.min_frames, int):
            raise ValueError("min_frames must be an integer")
        if max(self.confidence, self.keypoint_confidence, self.raise_margin_ratio) > 1:
            raise ValueError("confidence thresholds and raise_margin_ratio must be <= 1")


@dataclass
class _HelpState:
    since: float | None = None
    last_t: float | None = None
    frames: int = 0
    confidence: float = 1.0
    last_alert: float = -math.inf


class HelpGestureDetector:
    """Require continuous raised, extended arms away from the head on one tracked ID.

    A gap greater than max_gap_seconds, non-increasing timestamp, invalid pose,
    or lowered hand breaks persistence. These breaks preserve that ID's cooldown.
    retain() forgets absent IDs completely, matching the existing tracker whose
    expired IDs are never reused. reset() also clears cooldowns.
    """

    def __init__(self, config: HelpConfig):
        config.validate()
        self.config = config
        self.states: dict[str, _HelpState] = {}

    def reset(self) -> None:
        self.states.clear()

    def retain(self, ids: set[str]) -> None:
        self.states = {key: state for key, state in self.states.items() if key in ids}

    def _face_confidence(self, kp: np.ndarray) -> float | None:
        """Frontal-face geometry proxy; cannot determine where eyeballs look.

        Unreliable or implausible points mean unknown, which fails the optional
        gate. Ears are deliberately not required: hair can hide them frontally.
        """
        face = kp[[0, 1, 2]]
        if ((face[:, 2] < self.config.keypoint_confidence).any()
                or (face[:, 2] > 1).any()):
            return None
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            eyes = kp[[1, 2], :2]
            eyes = eyes[np.argsort(eyes[:, 0])]
            eye_axis = eyes[1] - eyes[0]
            eye_span = float(np.linalg.norm(eye_axis))
            shoulder_span = float(np.linalg.norm(kp[5, :2] - kp[6, :2]))
            if (not math.isfinite(eye_span) or not math.isfinite(shoulder_span)
                    or eye_span <= 0 or shoulder_span <= 0
                    or not _MIN_EYE_SHOULDER_RATIO <= eye_span/shoulder_span <= _MAX_EYE_SHOULDER_RATIO):
                return None
            axis = eye_axis/eye_span
            if abs(float(axis[1])) > math.sin(math.radians(_MAX_EYE_LINE_ROLL_DEGREES)):
                return None
            down = np.array([-axis[1], axis[0]])
            offset = (kp[0, :2] - eyes.mean(axis=0))/eye_span
            lateral, vertical = float(offset @ axis), float(offset @ down)
            if (not math.isfinite(lateral) or not math.isfinite(vertical)
                    or abs(lateral) > _MAX_NOSE_EYE_OFFSET_RATIO
                    or not 0.05 <= vertical <= 1.5):
                return None
        return float(face[:, 2].min())

    def facing_camera_confidence(self, pose: Pose) -> float | None:
        """Expose the proxy for preview diagnostics, separately from raised arms."""
        try:
            kp = np.asarray(pose.keypoints, dtype=float)
            if (kp.shape != (17, 3) or not np.isfinite(kp).all()
                    or not finite_box(pose.bbox) or not math.isfinite(pose.confidence)
                    or not self.config.confidence <= pose.confidence <= 1):
                return None
            if ((kp[[5, 6], 2] < self.config.keypoint_confidence).any()
                    or (kp[[5, 6], 2] > 1).any()):
                return None
            return self._face_confidence(kp)
        except (TypeError, ValueError, OverflowError):
            return None

    def _candidate_confidence(self, pose: Pose) -> float | None:
        c = self.config
        try:
            kp = np.asarray(pose.keypoints, dtype=float)
            if (kp.shape != (17, 3) or not np.isfinite(kp).all()
                    or not finite_box(pose.bbox)
                    or not math.isfinite(pose.confidence)
                    or not c.confidence <= pose.confidence <= 1):
                return None
            # COCO17: nose=0, shoulders=5/6, elbows=7/8, wrists=9/10.
            # Eyes also need confidence when the face filter is enabled.
            # All input must be finite.
            required = kp[[0, 5, 6, 7, 8, 9, 10]]
            if ((required[:, 2] < c.keypoint_confidence).any()
                    or (required[:, 2] > 1).any()):
                return None
            face_confidence = self._face_confidence(kp) if c.require_facing_camera else 1.0
            if face_confidence is None:
                return None
            reference_y = float(kp[[0, 5, 6], 1].min())
            margin = (pose.bbox[3] - pose.bbox[1]) * c.raise_margin_ratio
            if not (kp[[9, 10], 1] < reference_y - margin).all():
                return None
            # Raised arms can enlarge bbox width; side views compress shoulder
            # span, so retain nose-to-shoulder distance as a second scale cue.
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                nose = kp[0, :2]
                shoulders = kp[[5, 6], :2]
                elbows = kp[[7, 8], :2]
                wrists = kp[[9, 10], :2]
                scale = max(float(np.linalg.norm(shoulders[0] - shoulders[1])),
                            float(np.linalg.norm(nose - shoulders.mean(axis=0))))
                if not math.isfinite(scale) or scale <= 0:
                    return None
                if not ((wrists[:, 1] < elbows[:, 1]).all()
                        and (elbows[:, 1] < shoulders[:, 1]).all()):
                    return None
                head_distances = np.linalg.norm(wrists - nose, axis=1)
                if (not np.isfinite(head_distances).all()
                        or (head_distances <= scale * _MIN_HEAD_DISTANCE_RATIO).any()):
                    return None
                upper = shoulders - elbows
                forearms = wrists - elbows
                upper_lengths = np.linalg.norm(upper, axis=1)
                forearm_lengths = np.linalg.norm(forearms, axis=1)
                lengths = np.concatenate((upper_lengths, forearm_lengths))
                if (not np.isfinite(lengths).all()
                        or (lengths < scale * _MIN_ARM_SEGMENT_RATIO).any()):
                    return None
                elbow_cosines = np.sum((upper / upper_lengths[:, None])
                                       * (forearms / forearm_lengths[:, None]), axis=1)
                if (elbow_cosines > math.cos(math.radians(_MIN_ELBOW_ANGLE_DEGREES))).any():
                    return None
            return min(float(pose.confidence), float(required[:, 2].min()), face_confidence)
        except (TypeError, ValueError, OverflowError):
            # Malformed numeric input must not leave a candidate armed.
            return None

    def update(self, track_id: str, pose: Pose, t: float) -> Signal | None:
        c = self.config
        state = self.states.get(track_id)
        last_alert = state.last_alert if state is not None else -math.inf
        try:
            valid_time = not isinstance(t, bool) and math.isfinite(t)
        except (TypeError, ValueError, OverflowError):
            valid_time = False
        confidence = self._candidate_confidence(pose) if valid_time else None
        if confidence is None:
            if state is not None:
                self.states[track_id] = _HelpState(last_alert=last_alert)
            return None

        if (state is None or state.since is None or state.last_t is None
                or t <= state.last_t or t - state.last_t > c.max_gap_seconds):
            state = self.states[track_id] = _HelpState(
                since=t, last_t=t, frames=1, confidence=confidence,
                last_alert=last_alert,
            )
        else:
            state.last_t = t
            state.frames += 1
            state.confidence = min(state.confidence, confidence)

        elapsed = t - state.since
        if (state.frames < c.min_frames or elapsed + 1e-8 < c.hold_seconds
                or t - state.last_alert < c.cooldown_seconds):
            return None
        state.last_alert = t
        return Signal("help_requested", track_id, state.confidence, {
            "algorithm": ("temporal_two_hands_raised_extended_front_v3" if c.require_facing_camera
                          else "temporal_two_hands_raised_extended_v2"),
            "require_facing_camera": c.require_facing_camera,
            "face_condition": "frontal_nose_eye_geometry_proxy" if c.require_facing_camera else "disabled",
            "gaze_estimated": False,
            "gesture": "both_hands_raised_extended_away_from_head_candidate",
            "frames": state.frames,
            "hold_seconds": round(elapsed, 3),
            "reference": "higher_of_nose_and_shoulders_with_bbox_height_margin",
            "raise_margin_ratio": c.raise_margin_ratio,
            "arm_order": "wrist_above_elbow_above_shoulder_on_each_side",
            "min_elbow_angle_degrees": _MIN_ELBOW_ANGLE_DEGREES,
            "min_arm_segment_ratio": _MIN_ARM_SEGMENT_RATIO,
            "min_head_distance_ratio": _MIN_HEAD_DISTANCE_RATIO,
            "body_scale_reference": "max_shoulder_span_and_nose_to_shoulder_midpoint",
            "head_reference": "nose_centered_exclusion_radius_of_body_scale",
            "limitation": "Raised, extended arms away from the head candidate only; "
                          "2D geometry cannot establish hand/head contact; cannot infer "
                          "help-seeking intent or detect speech or sound; side views, "
                          "occlusion and bent arms may be missed. Optional frontal-face geometry "
                          "does not estimate eyeball gaze; a forward face can look elsewhere, "
                          "and missing face points fail the enabled filter.",
            "confidence_kind": "minimum_pose_and_required_keypoint_score_over_hold_"
                               "not_calibrated_event_probability",
        })

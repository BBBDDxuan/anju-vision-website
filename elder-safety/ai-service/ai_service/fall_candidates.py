"""Conservative proposal of foreshortened/cropped falls for visual review only.

A 2-D pose cannot confirm a fall toward the optical axis or beyond the frame.
These signals are enabled only by the Qwen candidate collector, never by the
standalone alarm engine. Missing/invalid poses discard transition history. A
separate, bounded inverted-posture buffer never transfers evidence across IDs.
"""
from collections import deque
from dataclasses import dataclass, field
import math

import numpy as np

from .config import FallConfig
from .types import Pose, Signal, finite_box, iou


@dataclass
class _Observation:
    t: float
    hip: tuple[float, float]
    shoulder: tuple[float, float]
    height: float
    ankle: tuple[float, float] | None
    angle: float
    upright: bool


@dataclass
class _State:
    history: deque = field(default_factory=deque)
    last_alert: float = -math.inf
    prone_since: float | None = None
    prone_frames: int = 0
    prone_reported: bool = False
    inverted_observations: deque = field(default_factory=lambda: deque(maxlen=32))
    inverted_tail: int = 0


class ReviewFallDetector:
    def __init__(self, config: FallConfig):
        self.config = config
        self.states: dict[str, _State] = {}
        self.recent_alerts: deque = deque(maxlen=64)

    def reset(self):
        self.states.clear()
        self.recent_alerts.clear()

    def retain(self, present: set[str], *, recoverable: set[str] | None = None):
        # Only the posture-only buffer can survive a missed detection while the
        # tracker still owns that ID. Never retain standing/descent/hold evidence.
        alive = present if recoverable is None else present | recoverable
        self.states = {key: state for key, state in self.states.items() if key in present or (key in alive and state.inverted_observations)}
        for key, state in self.states.items():
            if key not in present:
                state.history.clear()
                state.prone_since = None
                state.prone_frames = 0
                state.inverted_tail = 0

    def accept_strict(self, track_id: str, t: float, bbox=None) -> bool:
        state = self.states.setdefault(track_id, _State())
        if t - state.last_alert < self.config.cooldown_seconds:
            return False
        state.last_alert = t
        state.prone_reported = True
        if bbox is not None:
            self.recent_alerts.append((t, bbox))
        return True

    def update(self, track_id: str, pose: Pose, t: float, armed: bool,
               *, recovery_allowed: bool = True) -> Signal | None:
        c = self.config
        while self.recent_alerts and t - self.recent_alerts[0][0] >= c.cooldown_seconds:
            self.recent_alerts.popleft()
        kp = np.asarray(pose.keypoints)
        core = [5, 6, 11, 12]
        if (kp.shape != (17, 3) or not finite_box(pose.bbox)
                or not math.isfinite(pose.confidence) or not np.isfinite(kp[core]).all()
                or (kp[core, 2] < c.keypoint_confidence).any()):
            # Preserve cooldown, but no geometric evidence across an invalid frame.
            state = self.states.get(track_id)
            if state:
                state.history.clear()
                state.prone_since = None
                state.prone_frames = 0
                state.inverted_tail = 0
                # Unreliable points contribute no geometry. Retain only earlier
                # complete poses within the same unambiguous tracked region.
                if (not recovery_allowed or kp.shape != (17, 3)
                        or not finite_box(pose.bbox) or not math.isfinite(pose.confidence)
                        or not np.isfinite(kp).all()
                        or ((kp[:, 2] < 0) | (kp[:, 2] > 1)).any()
                        or (state.inverted_observations
                            and iou(pose.bbox, state.inverted_observations[-1][1]) < 0.3)):
                    state.inverted_observations.clear()
            return None
        shoulder, hip = kp[[5, 6], :2].mean(axis=0), kp[[11, 12], :2].mean(axis=0)
        dx, dy = hip - shoulder
        if math.hypot(dx, dy) < 5:
            state = self.states.get(track_id)
            if state:
                state.history.clear()
                state.prone_since = None
                state.prone_frames = 0
                state.inverted_observations.clear()
                state.inverted_tail = 0
            return None
        angle = math.degrees(math.atan2(abs(dx), abs(dy)))
        height = pose.bbox[3] - pose.bbox[1]
        ratio = (pose.bbox[2] - pose.bbox[0]) / height
        upright = dy > 0 and angle <= c.upright_angle_degrees and ratio < c.min_bbox_ratio
        ankles_valid = np.isfinite(kp[[15, 16]]).all() and bool((kp[[15, 16], 2] >= c.keypoint_confidence).all())
        ankle = tuple(map(float, kp[[15, 16], :2].mean(axis=0))) if ankles_valid else None
        state = self.states.setdefault(track_id, _State())
        if state.history and (t <= state.history[-1].t or t - state.history[-1].t > c.max_gap_seconds):
            state.history.clear()
            state.prone_since = None
            state.prone_frames = 0
        window = min(0.8, c.observation_seconds)
        while state.history and t - state.history[0].t > window + 1e-8:
            state.history.popleft()
        result = None
        if armed and t - state.last_alert >= c.cooldown_seconds:
            for previous in state.history:
                pt, ph = previous.t, previous.height
                elapsed = t - pt
                if not previous.upright or elapsed < 0.2 - 1e-8:
                    continue
                hip_drop = (float(hip[1]) - previous.hip[1]) / ph
                shoulder_drop = (float(shoulder[1]) - previous.shoulder[1]) / ph
                shoulder_motion = math.dist(shoulder, previous.shoulder) / ph
                hip_motion_speed = math.dist(hip, previous.hip) / ph / elapsed
                compression = height / ph
                foot_drop = ((ankle[1] - previous.ankle[1]) / ph
                             if ankle is not None and previous.ankle is not None else None)
                tilted = angle >= c.angle_degrees
                moving_feet = foot_drop is not None and foot_drop >= 0.18
                reason = None
                # Original rapid descent branch remains bounded to 0.6 seconds.
                if (elapsed <= 0.6 + 1e-8 and hip_drop >= max(c.min_hip_drop, 0.18)
                        and shoulder_drop >= 0.22 and hip_drop / elapsed >= c.min_drop_speed
                        and compression <= 0.75 and (tilted or moving_feet)):
                    reason = ('rapid_tilt_with_body_compression' if tilted
                              else 'foreshortened_forward_descent')
                previous_span = abs(previous.hip[1] - previous.shoulder[1])
                torso_ratio = abs(float(hip[1] - shoulder[1])) / max(previous_span, 5.)
                support_shift = None
                if ankle is not None and previous.ankle is not None:
                    before = previous.hip[0] - previous.ankle[0]
                    current = float(hip[0]) - ankle[0]
                    support_shift = abs(current - before) / ph
                    # Rotation and a shorter box also occur when kneeling or
                    # picking up objects. Require independent foot-support
                    # evidence: descending hips move relative to feet, or feet
                    # lift. Hip rise with planted feet is also a common bow.
                    lost_support = ((hip_drop >= 0.06 and support_shift >= 0.10)
                                    or foot_drop <= -0.15)
                    # Lateral/backward projection can move hips upward; do not
                    # require a downward trajectory in these camera views.
                    if (reason is None and lost_support
                            and angle >= max(40., c.upright_angle_degrees + 10.)
                            and angle - previous.angle >= 30.
                            and abs(hip_drop) >= 0.06 and abs(hip_drop) / elapsed >= 0.12
                            and shoulder_motion >= 0.15 and compression <= 0.75
                            and torso_ratio <= 0.75):
                        reason = 'rapid_lateral_rotation_and_body_collapse'
                    # Forward/backward falls can keep a near-vertical 2-D torso.
                    # Motion of hips relative to reliable feet distinguishes this
                    # candidate from ordinary vertical squatting or translation.
                    if (reason is None and hip_drop >= 0.15 and shoulder_drop >= 0.18
                            and hip_motion_speed >= c.min_drop_speed
                            and compression <= 0.75 and support_shift >= 0.18
                            and abs(current) / ph >= 0.15):
                        reason = 'rapid_descent_outside_foot_support'
                if reason is None:
                    continue
                state.last_alert = t
                confidence = min(float(pose.confidence), float(kp[core, 2].min()))
                result = Signal('suspected_fall', track_id, confidence, {
                    'algorithm': 'rapid_collapse_review_v3', 'review_required': True,
                    'candidate_reason': reason,
                    'reference_t': round(pt, 6), 'observation_seconds': round(elapsed, 6),
                    'hip_drop_normalized': round(hip_drop, 3),
                    'shoulder_drop_normalized': round(shoulder_drop, 3),
                    'bbox_height_ratio': round(compression, 3), 'torso_angle_deg': round(angle, 3),
                    'foot_drop_normalized': round(foot_drop, 3) if foot_drop is not None else None,
                    'torso_vertical_span_ratio': round(torso_ratio, 3),
                    'support_shift_normalized': round(support_shift, 3) if support_shift is not None else None,
                    'hip_motion_speed_normalized': round(hip_motion_speed, 3),
                    'confidence_kind': 'minimum_pose_and_core_keypoint_score_not_calibrated_event_probability',
                    'limitation': 'Rapid collapse candidate only; no continuous prone hold observed. Requires multi-frame visual review; a cropped or obscured subject may remain uncertain.',
                })
                break
        # A recovered skeleton may belong to someone already lying down. Do
        # not invent the missing descent or carry standing across a lost track.
        # Instead require a fresh, continuous, reliable whole-body prone hold
        # and send an explicitly uncertain posture candidate for visual review.
        prone = False
        inverted = False
        if ankle is not None:
            span = abs(float(shoulder[0]) - ankle[0])
            vertical = max(float(shoulder[1]), float(hip[1]), ankle[1]) - min(float(shoulder[1]), float(hip[1]), ankle[1])
            ordered = (float(shoulder[0]) - float(hip[0])) * (float(hip[0]) - ankle[0]) > 0
            prone = (angle >= c.angle_degrees and ratio >= c.min_bbox_ratio
                     and span >= max(20., 0.6 * (pose.bbox[2] - pose.bbox[0]))
                     and vertical <= 0.55 * span and ordered)
            # In an oblique camera view a body lying toward the lens may be
            # tall/narrow, with shoulders below hips and feet above both. A
            # reliable full-body ordering is different from bending at the waist.
            inverted = (float(shoulder[1]) > float(hip[1]) + max(5., 0.1 * height)
                        and float(hip[1]) > ankle[1] + 5.
                        and float(shoulder[1]) - ankle[1] >= 0.5 * height)
            prone = prone or inverted
        # Track IDs may change during occlusion. Suppress a redundant posture
        # proposal overlapping a recent fall candidate, without borrowing any
        # identity or standing evidence to manufacture a new fall transition.
        if prone and any(0 <= t - at < c.cooldown_seconds and iou(pose.bbox, box) >= 0.3
                         for at, box in self.recent_alerts):
            state.prone_reported = True
        if not prone:
            state.prone_since = None
            state.prone_frames = 0
        else:
            if state.prone_since is None:
                state.prone_since = t
            state.prone_frames += 1
            hold = t - state.prone_since
            if (result is None and not state.prone_reported
                    and state.prone_frames >= 3 and hold + 1e-8 >= max(0.8, c.hold_seconds)
                    and t - state.last_alert >= c.cooldown_seconds):
                confidence = min(float(pose.confidence), float(kp[core + [15, 16], 2].min()))
                result = Signal('suspected_fall', track_id, confidence, {
                    'algorithm': 'sustained_prone_review_v1', 'review_required': True,
                    'candidate_reason': ('sustained_inverted_body_without_observed_transition' if inverted
                                         else 'sustained_prone_without_observed_transition'),
                    'posture_projection': 'head_below_hips_and_feet' if inverted else 'horizontal',
                    'fall_transition_observed': False,
                    'hold_seconds': round(hold, 6), 'frames': state.prone_frames,
                    'torso_angle_deg': round(angle, 3), 'bbox_ratio': round(ratio, 3),
                    'whole_body_vertical_horizontal_ratio': round(vertical / span, 3) if not inverted else None,
                    'confidence_kind': 'minimum_pose_and_required_keypoint_score_not_calibrated_event_probability',
                    'limitation': 'Sustained lying posture only; no fall transition or floor contact proven. Voluntary lying, bed or sofa use, handstands or an inverted camera require multi-frame visual review; uncertain is not a confirmed alarm.',
                })
                state.last_alert = t
        # Intermittent inverted skeletons are not a continuous prone hold.
        # Recover a review proposal only from five reliable whole-body poses,
        # including a fresh three-frame tail, on this very same tracker ID.
        observations = state.inverted_observations
        if not inverted or not recovery_allowed or pose.confidence < 0.35:
            observations.clear()
            state.inverted_tail = 0
        else:
            window = min(2.5, c.observation_seconds)
            while observations and t - observations[0][0] > window + 1e-8:
                observations.popleft()
            if observations and (t <= observations[-1][0]
                    or t - observations[-1][0] > min(1.0, window) + 1e-8
                    or iou(pose.bbox, observations[-1][1]) < 0.3):
                observations.clear()
                state.inverted_tail = 0
            confidence = min(float(pose.confidence), float(kp[core + [15, 16], 2].min()))
            observations.append((t, pose.bbox, confidence))
            state.inverted_tail += 1
            elapsed = t - observations[0][0]
            if (result is None and not state.prone_reported and len(observations) >= 5
                    and state.inverted_tail >= 3 and elapsed + 1e-8 >= max(0.8, c.hold_seconds)
                    and t - state.last_alert >= c.cooldown_seconds):
                gaps = [b[0] - a[0] for a, b in zip(observations, list(observations)[1:])]
                result = Signal('suspected_fall', track_id, min(o[2] for o in observations), {
                    'algorithm': 'interrupted_inverted_pose_review_v1', 'review_required': True,
                    'candidate_reason': 'repeated_inverted_body_after_pose_loss',
                    'posture_projection': 'head_below_hips_and_feet',
                    'fall_transition_observed': False,
                    'observation_span_seconds': round(elapsed, 6),
                    'reliable_frames': len(observations),
                    'consecutive_reliable_frames': state.inverted_tail,
                    'largest_observation_gap_seconds': round(max(gaps), 6),
                    'torso_angle_deg': round(angle, 3), 'bbox_ratio': round(ratio, 3),
                    'confidence_kind': 'minimum_pose_and_required_keypoint_score_not_calibrated_event_probability',
                    'limitation': 'Intermittent complete inverted poses on one spatially unambiguous track only; missing intervals are not observed lying time. No descent, floor contact or continuous prone hold proven. Requires multi-frame visual review.',
                })
                state.last_alert = t
        if result is not None:
            state.prone_reported = True
            self.recent_alerts.append((t, pose.bbox))
        # Rearm posture proposals only after a continuous upright period.
        if (upright and state.history and t - state.history[0].t >= c.min_upright_seconds
                and all(item.upright for item in state.history)):
            state.prone_reported = False
        state.history.append(_Observation(t, tuple(map(float, hip)), tuple(map(float, shoulder)),
                                          height, ankle, angle, upright))
        return result

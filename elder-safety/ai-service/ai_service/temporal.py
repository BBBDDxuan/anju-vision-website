from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import numpy as np

from .config import FallConfig, FireConfig
from .types import Flame, Pose, Signal, finite_box, iou


@dataclass
class FallState:
    history: deque = field(default_factory=deque)
    upright_since: float | None = None
    armed_at: float | None = None
    anchor_y: float = 0
    anchor_height: float = 1
    candidate_at: float | None = None
    horizontal_since: float | None = None
    last_t: float | None = None
    last_alert: float = -math.inf
    peak_speed: float = 0
    motion_at: float | None = None
    peak_rotation_speed: float = 0


class FallDetector:
    def __init__(self, config: FallConfig):
        self.config = config
        self.states: dict[str, FallState] = {}
        self.diagnostics: dict[str, dict] = {}

    def reset(self):
        self.states.clear()
        self.diagnostics.clear()

    def retain(self, present: set[str]):
        # A single absent detection/invalid pose disarms that track: no gap bridging.
        self.states = {k: v for k, v in self.states.items() if k in present}
        self.diagnostics = {k: v for k, v in self.diagnostics.items() if k in present}

    def update(self, track_id: str, pose: Pose, t: float) -> Signal | None:
        c = self.config
        diagnostic = self.diagnostics[track_id] = {"track_id": track_id, "state": "keypoints_unreliable"}
        kp = np.asarray(pose.keypoints)
        if (kp.shape != (17, 3) or not finite_box(pose.bbox)
                or not np.isfinite(kp[[5, 6, 11, 12]]).all()
                or (kp[[5, 6, 11, 12], 2] < c.keypoint_confidence).any()
                or not math.isfinite(pose.confidence)):
            self.states.pop(track_id, None)
            return None
        shoulder = kp[[5, 6], :2].mean(axis=0)
        hip = kp[[11, 12], :2].mean(axis=0)
        dx, dy = hip - shoulder
        if math.hypot(dx, dy) < 5:
            self.states.pop(track_id, None)
            return None
        angle = math.degrees(math.atan2(abs(dx), abs(dy)))
        x1, y1, x2, y2 = pose.bbox
        ratio = (x2-x1)/(y2-y1)
        diagnostic.update(torso_angle_deg=round(angle, 1), bbox_ratio=round(ratio, 2))
        s = self.states.setdefault(track_id, FallState())
        if s.last_t is not None and (t <= s.last_t or t-s.last_t > c.max_gap_seconds):
            s = self.states[track_id] = FallState(last_alert=s.last_alert)
        s.last_t = t
        s.history.append((t, float(hip[1]), angle))
        while s.history and t-s.history[0][0] > c.observation_seconds:
            s.history.popleft()
        upright = dy > 0 and angle <= c.upright_angle_degrees and ratio < c.min_bbox_ratio
        if upright:
            # Even a brief upright frame breaks a continuous prone hold.
            s.candidate_at = None
            s.horizontal_since = None
            if s.upright_since is None:
                s.upright_since = t
                # Freeze the standing reference before a gradual loss of balance.
                # Replacing it on every tilted upright frame hides the initial drop.
                s.anchor_y = float(hip[1])
                s.anchor_height = y2-y1
                s.motion_at = None
                s.peak_speed = 0
                s.peak_rotation_speed = 0
            if t-s.upright_since >= c.min_upright_seconds:
                s.armed_at = t
                s.candidate_at = None
                s.horizontal_since = None
        else:
            s.upright_since = None
        # Use recent pairs; retain rapid descent briefly while distance accumulates.
        speed = rotation_speed = 0.0
        if len(s.history) >= 2:
            pt, py, previous_angle = s.history[-2]
            speed = (float(hip[1])-py)/s.anchor_height/(t-pt)
            rotation_speed = max(0, (angle-previous_angle)/(t-pt))
        drop = (float(hip[1])-s.anchor_y)/s.anchor_height
        diagnostic.update(hip_drop_normalized=round(drop, 3), drop_speed_normalized=round(speed, 3))
        motion_window = min(0.5, c.observation_seconds)
        if s.candidate_at is None and s.motion_at is not None and t-s.motion_at > motion_window:
            s.motion_at, s.peak_speed, s.peak_rotation_speed = None, 0, 0
        if (s.armed_at is not None and drop > 0
                and (speed >= c.min_drop_speed or rotation_speed >= c.min_rotation_speed)):
            # Projected hip motion can be small in an oblique view. Rapid tilt
            # supplies motion evidence, but never replaces minimum hip drop,
            # horizontal posture, prior standing or the continuous prone hold.
            s.motion_at = t
            s.peak_speed = max(speed, s.peak_speed)
            s.peak_rotation_speed = max(rotation_speed, s.peak_rotation_speed)
        if upright:
            diagnostic["state"] = "ready" if s.armed_at is not None else "observing_standing"
            return None
        if s.armed_at is None:
            diagnostic["state"] = "need_standing"
            return None
        diagnostic["state"] = "observing_descent"
        if s.candidate_at is None:
            if t-s.armed_at > c.observation_seconds:
                s.armed_at = None
                diagnostic["state"] = "need_standing"
                return None
            if s.motion_at is not None and drop >= c.min_hip_drop:
                s.candidate_at = t
        if s.candidate_at is None:
            return None
        if t-s.candidate_at > c.observation_seconds:
            s.armed_at = None
            s.candidate_at = None
            diagnostic["state"] = "need_standing"
            return None
        horizontal = angle >= c.angle_degrees and ratio >= c.min_bbox_ratio and drop >= c.min_hip_drop
        if not horizontal:
            diagnostic["state"] = "confirming_posture"
            s.horizontal_since = None
            return None
        diagnostic["state"] = "confirming_hold"
        if s.horizontal_since is None:
            s.horizontal_since = t
        if t-s.horizontal_since + 1e-8 < c.hold_seconds:
            return None
        if t-s.last_alert < c.cooldown_seconds:
            diagnostic["state"] = "cooldown"
            return None
        diagnostic["state"] = "alert"
        s.last_alert = t
        s.armed_at = None  # must stand upright again to rearm
        return Signal("suspected_fall", track_id, min(1.0, max(0.0, pose.confidence)), {
            "algorithm": "temporal_pose_v2", "motion_window_seconds": motion_window, "torso_angle_deg": round(angle, 3),
            "hip_drop_normalized": round(drop, 3), "peak_drop_speed": round(s.peak_speed, 3),
            "peak_rotation_speed_deg_s": round(s.peak_rotation_speed, 3),
            "bbox_ratio": round(ratio, 3), "hold_seconds": round(t-s.horizontal_since, 3),
            "confidence_kind": "pose_detector_score_not_calibrated_event_probability",
        })


@dataclass
class FireState:
    box: tuple
    since: float
    last_t: float
    count: int = 1
    last_alert: float = -math.inf


class FireDetector:
    def __init__(self, config: FireConfig):
        self.config = config
        self.states: dict[str, FireState] = {}
        self.next_id = 1

    def reset(self):
        self.states.clear()

    def update(self, detections: list[Flame], t: float) -> list[Signal]:
        c = self.config
        current = {}
        signals = []
        for detection in sorted(detections, key=lambda d: d.confidence, reverse=True):
            if (not math.isfinite(detection.confidence) or detection.confidence < c.confidence
                    or not finite_box(detection.bbox)):
                continue
            best = None
            best_iou = c.region_iou
            for key, state in self.states.items():
                overlap = iou(state.box, detection.bbox)
                if (key not in current and 0 < t-state.last_t <= c.max_gap_seconds
                        and overlap >= best_iou):
                    best, best_iou = key, overlap
            if best is None:
                best = f"fire-{self.next_id}"
                self.next_id += 1
                state = FireState(detection.bbox, t, t)
            else:
                state = self.states[best]
                state.count += 1
                state.last_t = t
                state.box = detection.bbox
            current[best] = state
            if (state.count >= c.min_frames and t-state.since + 1e-8 >= c.hold_seconds
                    and t-state.last_alert >= c.cooldown_seconds):
                state.last_alert = t
                signals.append(Signal("fire_detected", best, min(1.0, detection.confidence), {
                    "algorithm": "persistent_flame_region_v1", "label": detection.label,
                    "frames": state.count, "hold_seconds": round(t-state.since, 3),
                    "limitation": "TV imagery and lamps cannot be reliably distinguished",
                    "confidence_kind": "flame_detector_score_not_calibrated_event_probability",
                }))
        self.states = current  # missing regions break persistence immediately
        return signals

"""Oblique-view raised/waving hands: review proposals, never standalone alarms.

Both hands must stay raised and outside a smaller head exclusion region, with
reliable arms and temporal waving evidence relative to the shoulders. Bent
elbows are allowed. Neither geometry nor waving establishes help-seeking intent.
"""
from collections import deque
from dataclasses import dataclass, field
import math

import numpy as np

from .help_gesture import HelpConfig, HelpGestureDetector
from .types import Pose, Signal, finite_box


@dataclass
class _State:
    history: deque = field(default_factory=deque)
    last_t: float | None = None
    last_alert: float = -math.inf


class ReviewHelpDetector:
    def __init__(self, config: HelpConfig):
        config.validate()
        self.config = config
        self.face = HelpGestureDetector(config)
        self.states: dict[str, _State] = {}

    def reset(self):
        self.states.clear()

    def retain(self, present: set[str]):
        self.states = {k: s for k, s in self.states.items() if k in present}

    def accept_strict(self, track_id: str, t: float) -> bool:
        state = self.states.setdefault(track_id, _State())
        if t - state.last_alert < self.config.cooldown_seconds:
            return False
        state.last_alert = t
        return True

    def _geometry(self, pose: Pose):
        c = self.config
        try:
            kp = np.asarray(pose.keypoints, dtype=float)
            if (kp.shape != (17, 3) or not np.isfinite(kp).all()
                    or not finite_box(pose.bbox) or not math.isfinite(pose.confidence)
                    or not c.confidence <= pose.confidence <= 1):
                return None
            required = kp[[0, 5, 6, 7, 8, 9, 10]]
            if ((required[:, 2] < c.keypoint_confidence).any()
                    or (required[:, 2] > 1).any()):
                return None
            face = self.face._face_confidence(kp) if c.require_facing_camera else 1.0
            if face is None:
                return None
            nose, shoulders = kp[0, :2], kp[[5, 6], :2]
            elbows, wrists = kp[[7, 8], :2], kp[[9, 10], :2]
            center = shoulders.mean(axis=0)
            scale = max(float(np.linalg.norm(shoulders[0] - shoulders[1])),
                        float(np.linalg.norm(nose - center)))
            if not math.isfinite(scale) or scale < 5:
                return None
            # Use shoulder/body scale instead of a full-person bbox margin:
            # perspective shortens the projected nose-to-hand distance.
            if (not (wrists[:, 1] < shoulders[:, 1] - 0.25 * scale).all()
                    or not (wrists[:, 1] < nose[1] + 0.15 * scale).all()
                    or not (wrists[:, 1] < elbows[:, 1]).all()
                    or (np.linalg.norm(wrists - nose, axis=1) <= 0.55 * scale).any()
                    or np.linalg.norm(wrists[0] - wrists[1]) < 0.8 * scale):
                return None
            upper, forearm = shoulders - elbows, wrists - elbows
            ul, fl = np.linalg.norm(upper, axis=1), np.linalg.norm(forearm, axis=1)
            if (np.r_[ul, fl] < 0.2 * scale).any():
                return None
            cosines = np.sum(upper / ul[:, None] * forearm / fl[:, None], axis=1)
            if (cosines > math.cos(math.radians(60))).any():
                return None
            return ((wrists - center) / scale,
                    min(pose.confidence, float(required[:, 2].min()), face))
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _waving(points):
        # Both hands must move, and at least one must reverse direction with
        # an excursion larger than keypoint jitter. Body translation cancels.
        extent = np.linalg.norm(np.ptp(points, axis=0), axis=1)
        if (extent < 0.3).any():
            return False
        for hand in range(2):
            for axis in range(2):
                values = points[:, hand, axis]
                for i in range(1, len(values) - 1):
                    before, after = values[:i], values[i + 1:]
                    if ((values[i] - before.min() >= 0.15 and values[i] - after.min() >= 0.15)
                            or (before.max() - values[i] >= 0.15 and after.max() - values[i] >= 0.15)):
                        return True
        return False

    def update(self, track_id: str, pose: Pose, t: float) -> Signal | None:
        state = self.states.setdefault(track_id, _State())
        c = self.config
        try:
            valid_t = not isinstance(t, bool) and math.isfinite(t)
        except (TypeError, ValueError, OverflowError):
            valid_t = False
        geometry = self._geometry(pose) if valid_t else None
        if geometry is None:
            state.history.clear()
            state.last_t = None
            return None
        if state.last_t is not None and (t <= state.last_t or t - state.last_t > c.max_gap_seconds):
            state.history.clear()
        state.last_t = t
        points, confidence = geometry
        state.history.append((t, points, confidence))
        # Bound both duration and memory. Invalid/lowered hands reset immediately.
        window = max(1.5, c.hold_seconds)
        while state.history and state.history[0][0] < t - window - 1e-8:
            state.history.popleft()
        while len(state.history) > 128:
            state.history.popleft()
        elapsed = t - state.history[0][0]
        if (len(state.history) < c.min_frames or elapsed + 1e-8 < max(0.75, c.hold_seconds)
                or t - state.last_alert < c.cooldown_seconds
                or not self._waving(np.asarray([p for _, p, _ in state.history]))):
            return None
        state.last_alert = t
        return Signal("help_requested", track_id, min(s[2] for s in state.history), {
            "algorithm": "oblique_two_raised_waving_hands_review_v1",
            "review_required": True,
            "candidate_reason": "persistent_two_raised_hands_with_waving_motion",
            "gesture": "both_hands_raised_waving_candidate",
            "frames": len(state.history), "hold_seconds": round(elapsed, 3),
            "require_facing_camera": c.require_facing_camera,
            "gaze_estimated": False,
            "limitation": "Bent arms and perspective allowed for review only; waving can be "
                          "stretching or greeting, not proof of help-seeking intent or sound.",
            "confidence_kind": "pose_and_keypoint_score_not_calibrated_event_probability",
        })

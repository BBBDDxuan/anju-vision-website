"""Raised single/both-arm motion: review proposals, never standalone alarms.

The strict two-hand detector remains independent. This path uses a body-relative
head direction, reliable per-arm history and repeated wrist excursions. It does
not require frontal eyes and cannot establish help-seeking intent or sound.
"""

from dataclasses import replace
import math

import numpy as np

from .help_gesture import HelpConfig
from .help_motion import MotionConfig, MotionHelpDetector
from .types import Pose, Signal, finite_box


class ReviewHelpDetector:
    def __init__(self, config: HelpConfig):
        config.validate()
        self.config = config
        # Keep the measured motion thresholds separate from the stricter static
        # detector. Honour stronger deployment persistence/gap/count settings.
        self.motion = MotionHelpDetector(
            replace(
                MotionConfig(),
                person_confidence=config.confidence,
                window_seconds=max(3.0, config.hold_seconds),
                minimum_seconds=max(1.0, config.hold_seconds),
                minimum_samples=max(6, config.min_frames),
                max_gap_seconds=min(0.35, config.max_gap_seconds),
                cooldown_seconds=config.cooldown_seconds,
            )
        )

    @property
    def states(self):
        return self.motion.states

    def reset(self):
        self.motion.reset()

    def retain(self, present: set[str]):
        self.motion.retain(present)

    def accept_strict(self, track_id: str, t: float) -> bool:
        if (
            t - self.motion.last_alert.get(track_id, -math.inf)
            < self.config.cooldown_seconds
        ):
            return False
        self.motion.last_alert[track_id] = t
        return True

    def update(self, track_id: str, pose: Pose, t: float) -> Signal | None:
        try:
            valid_box = finite_box(pose.bbox)
        except (TypeError, ValueError, OverflowError):
            valid_box = False
        if not valid_box:
            self.motion.states.pop(track_id, None)
            return None
        candidate = self.motion.update(
            track_id, {"keypoints": pose.keypoints, "confidence": pose.confidence}, t
        )
        if candidate is None:
            return None
        hands = candidate["active_hands"]
        required = [0, 5, 6, 11, 12]
        for hand in hands:
            required.extend((5, 7, 9) if hand == 0 else (6, 8, 10))
        kp = np.asarray(pose.keypoints)
        confidence = min(pose.confidence, float(kp[required, 2].min()))
        # Preserve the existing event schema; review_required is mandatory.
        return Signal(
            "help_requested",
            track_id,
            confidence,
            {
                "algorithm": candidate["algorithm"],
                "review_required": True,
                "candidate_reason": "body_relative_repeated_raised_arm_motion",
                "gesture": "raised_arm_waving_candidate",
                "active_hands": ["left" if hand == 0 else "right" for hand in hands],
                "raised_fraction": candidate["raised_fraction"],
                "motion": candidate["motion"],
                "turns": candidate["turns"],
                "hand_spans": candidate["hand_spans"],
                "frames": len(self.motion.states[track_id]),
                "hold_seconds": round(candidate["span"], 3),
                "motion_window_seconds": self.motion.config.window_seconds,
                "require_facing_camera": False,
                "gaze_estimated": False,
                "limitation": candidate["limitation"],
                "confidence_kind": "pose_and_keypoint_score_not_calibrated_event_probability",
            },
        )

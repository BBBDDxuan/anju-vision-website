"""COCO17 body-relative arm-motion proposals, never help-intent verdicts.

Only pose coordinates/confidence and monotonic track time are used. No file
name, dataset label, person identity, or image appearance enters the rule.
"""

from dataclasses import dataclass
from collections import deque
import math
import numpy as np


@dataclass(frozen=True)
class MotionConfig:
    window_seconds: float = 3.0
    minimum_seconds: float = 1.0
    minimum_samples: int = 6
    max_gap_seconds: float = 0.35
    keypoint_confidence: float = 0.50
    person_confidence: float = 0.60
    raised_fraction: float = 0.50
    min_head_distance: float = 0.80
    min_head_advance: float = 0.25
    min_shoulder_advance: float = 0.60
    min_elbow_angle: float = 55.0
    minimum_motion: float = 0.65
    turn_excursion: float = 0.25
    minimum_turns: int = 2
    cooldown_seconds: float = 15.0


def count_turns(values, excursion):
    """Hysteresis counts completed reversals rather than tiny sign changes."""
    if len(values) < 3:
        return 0
    direction = 0
    extreme = float(values[0])
    turns = 0
    for v in values[1:]:
        if direction == 0:
            delta = v - extreme
            if abs(delta) >= excursion:
                direction = 1 if delta > 0 else -1
                extreme = v
        elif direction > 0:
            if v > extreme:
                extreme = v
            elif extreme - v >= excursion:
                turns += 1
                direction = -1
                extreme = v
        else:
            if v < extreme:
                extreme = v
            elif v - extreme >= excursion:
                turns += 1
                direction = 1
                extreme = v
    return turns


def smooth3(points):
    if len(points) < 3:
        return points.copy()
    padded = np.pad(points, ((1, 1), (0, 0)), mode="edge")
    return np.median(np.stack([padded[:-2], padded[1:-1], padded[2:]]), axis=0)


def pose_features(p, c):
    try:
        k = np.asarray(p["keypoints"], dtype=float)
        if isinstance(p["confidence"], bool) or (
            k.shape != (17, 3)
            or not np.isfinite(k).all()
            or not c.person_confidence <= p["confidence"] <= 1
        ):
            return None
        if np.any(k[:, 2] < 0) or np.any(k[:, 2] > 1):
            return None
        shoulders = k[[5, 6], :2]
        hips = k[[11, 12], :2]
        nose = k[0, :2]
        shoulder_center = shoulders.mean(axis=0)
        torso = shoulder_center - hips.mean(axis=0)
        length = float(np.linalg.norm(torso))
        width = float(np.linalg.norm(shoulders[0] - shoulders[1]))
        scale = max(width, 0.5 * length)
        if scale < 8 or length < 8:
            return None
        if min(k[[0, 5, 6, 11, 12], 2]) < c.keypoint_confidence:
            return None
        up = torso / length
        # Keep the anatomical axis, never the image's global vertical.
        head_projection = float((nose - shoulder_center) @ up / scale)
        if not -0.25 <= head_projection <= 1.5:
            return None
        points = []
        raised = []
        reliable = []
        head_distances = []
        for sh, el, wr in [(5, 7, 9), (6, 8, 10)]:
            s, e, w = k[[sh, el, wr], :2]
            a = s - e
            b = w - e
            ul = float(np.linalg.norm(a))
            fl = float(np.linalg.norm(b))
            q = float(k[[sh, el, wr], 2].min())
            reliable.append(q >= c.keypoint_confidence)
            angle = math.degrees(
                math.acos(float(np.clip(a @ b / max(ul * fl, 1e-9), -1, 1)))
            )
            dist = float(np.linalg.norm(w - nose) / scale)
            geometry = (
                q >= c.keypoint_confidence
                and 0.2 * scale <= min(ul, fl)
                and max(ul, fl) <= 3 * scale
                and angle >= c.min_elbow_angle
                and dist >= c.min_head_distance
                and float((w - nose) @ up / scale) >= c.min_head_advance
                and float((w - s) @ up / scale) >= c.min_shoulder_advance
            )
            raised.append(bool(geometry))
            head_distances.append(dist)
            points.append((w - shoulder_center) / scale)
        return {
            "points": np.asarray(points),
            "raised": np.asarray(raised),
            "reliable": np.asarray(reliable),
            "head_distances": head_distances,
            "scale": scale,
            "head_projection": head_projection,
        }
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


class MotionHelpDetector:
    def __init__(self, config=None):
        self.config = config or MotionConfig()
        self.states = {}
        self.last_alert = {}

    def reset(self):
        self.states.clear()
        self.last_alert.clear()

    def retain(self, present):
        self.states = {k: v for k, v in self.states.items() if k in present}
        self.last_alert = {k: v for k, v in self.last_alert.items() if k in present}

    def update(self, track, pose, t):
        c = self.config
        if (
            isinstance(t, bool)
            or not isinstance(t, (int, float))
            or not math.isfinite(t)
        ):
            self.states.pop(track, None)
            return None
        f = pose_features(pose, c)
        state = self.states.setdefault(track, deque())
        if state and (t <= state[-1]["t"] or t - state[-1]["t"] > c.max_gap_seconds):
            state.clear()
        # An unreliable frame cannot contribute a good pose or renew its history.
        if f is None:
            self.states.pop(track, None)
            return None
        state.append({"t": t, **f})
        while state and (
            t - state[0]["t"] > c.window_seconds + 1e-8 or len(state) > 128
        ):
            state.popleft()
        if (
            len(state) < c.minimum_samples
            or t - state[0]["t"] < c.minimum_seconds - 1e-8
        ):
            return None
        pts = np.stack([x["points"] for x in state])
        high = np.stack([x["raised"] for x in state])
        reliable = np.stack([x["reliable"] for x in state])
        # A side must be high across the window, not borrow the other side's hits.
        fractions = []
        motion = []
        turns = []
        spans = []
        for hand in range(2):
            # Ignore the initial lift from a hanging arm: it is not a wave.
            # Low-confidence wrist/elbow coordinates must not become motion evidence.
            bad = np.flatnonzero(~reliable[:, hand])
            reliable_start = int(bad[-1]) + 1 if len(bad) else 0
            starts = np.flatnonzero(high[reliable_start:, hand]) + reliable_start
            start = int(starts[0]) if len(starts) else len(high) - 1
            fraction = float(high[start:, hand].mean())
            fractions.append(fraction)
            spans.append(t - state[start]["t"])
            p = smooth3(pts[start:, hand, :])
            extent = float(
                np.linalg.norm(
                    np.percentile(p, 90, axis=0) - np.percentile(p, 10, axis=0)
                )
            )
            turn = max(count_turns(p[:, axis], c.turn_excursion) for axis in range(2))
            motion.append(extent)
            turns.append(turn)
        active = [
            i
            for i in range(2)
            if spans[i] >= c.minimum_seconds - 1e-8
            and fractions[i] >= c.raised_fraction
            and motion[i] >= c.minimum_motion
            and turns[i] >= c.minimum_turns
            and bool(high[-1, i])
        ]
        if not active or t - self.last_alert.get(track, -math.inf) < c.cooldown_seconds:
            return None
        self.last_alert[track] = t
        return {
            "event_type": "help_candidate",
            "review_required": True,
            "algorithm": "body_relative_repeated_arm_motion_review_v2",
            "track": track,
            "t": t,
            "active_hands": active,
            "raised_fraction": fractions,
            "motion": motion,
            "turns": turns,
            "span": t - state[0]["t"],
            "hand_spans": spans,
            "limitation": "Pose motion candidate only. Stretching, greeting and help intent can overlap; require visual review.",
        }

"""Bound review episodes without treating a proposal as a completed alarm."""
from collections import deque
from dataclasses import dataclass, field
import math

import numpy as np

from .review_recovery import SeatPolicy, TemporalPolicy, append_observed, body_projection, observation
from .types import Signal


@dataclass
class Episode:
    number: int
    started: float
    reference: dict
    progression_sent: bool = False
    normal: deque = field(default_factory=deque)
    progression: deque = field(default_factory=deque)
    last_t: float | None = None


class ReviewLifecycle:
    def __init__(self, config):
        self.config = config
        self.episodes = {}
        self.latest = {}
        self.counter = 0

    def reset(self):
        self.episodes.clear()
        self.latest.clear()
        # Keep numbering unique across discontinuities within one collector.

    def retain(self, recoverable, present):
        self.episodes = {k: v for k, v in self.episodes.items() if k in recoverable}
        for key, episode in self.episodes.items():
            if key not in present:
                episode.normal.clear()
                episode.progression.clear()
                episode.last_t = None
        self.latest.clear()

    def observe(self, key, pose, t, shape, allowed=True):
        """Return one observed progression and whether normal recovery rearms."""
        c = self.config
        o = observation(pose, shape, c.keypoint_confidence, SeatPolicy()) if allowed else None
        episode = self.episodes.get(key)
        if o is None:
            if episode:
                episode.normal.clear()
                episode.progression.clear()
            return None, False
        o = {**o, 't': t}
        self.latest[key] = o
        if episode is None:
            return None, False
        if episode.last_t is not None and t - episode.last_t > min(.2, c.max_gap_seconds) + 1e-8:
            episode.normal.clear()
            episode.progression.clear()
        episode.last_t = t
        v = o['hip'] - o['shoulder']
        angle = math.degrees(math.atan2(abs(v[0]), abs(v[1])))
        k = np.asarray(pose.keypoints)
        h, w = shape[:2]
        # Torso projection and straight legs alone also occur when lying toward
        # the camera. Recovery needs an observed foot below its own hip.
        foot_below_hip = any(k[ankle, 2] >= c.keypoint_confidence
                            and 0 <= k[ankle, 0] <= w and 0 <= k[ankle, 1] <= h
                            and k[ankle, 1] >= k[hip, 1] + 5
                            for hip, ankle in ((11, 15), (12, 16)))
        normal = foot_below_hip and (o['seated_proxy'] or (v[1] > 0 and angle <= c.upright_angle_degrees
                  and o['knee_extension'] is not None and o['knee_extension'] >= 145
                  and (pose.bbox[2] - pose.bbox[0]) / o['height'] < c.min_bbox_ratio))
        if normal:
            append_observed(episode.normal, o, c.min_upright_seconds)
            first = episode.normal[0]
            if (len(episode.normal) >= 3 and t - first['t'] >= c.min_upright_seconds - 1e-8
                    and all(np.linalg.norm(a['hip'] - first['hip']) / first['height'] <= .08
                            and np.linalg.norm(a['shoulder'] - first['shoulder']) / first['height'] <= .10
                            for a in episode.normal)):
                del self.episodes[key]
                return None, True
        else:
            episode.normal.clear()
        if t - episode.started >= c.cooldown_seconds:
            del self.episodes[key]
            return None, False
        if episode.progression_sent:
            return None, False
        ref = episode.reference
        shoulder_drop = float((o['shoulder'][1] - ref['shoulder'][1]) / ref['height'])
        hip_drop = float((o['hip'][1] - ref['hip'][1]) / ref['height'])
        p = SeatPolicy()
        geometry = body_projection(pose, shape, c.keypoint_confidence, TemporalPolicy())
        stronger = ((shoulder_drop >= p.core_shoulder_drop and hip_drop >= p.core_hip_drop)
                    or (geometry is not None and
                        (shoulder_drop >= p.core_shoulder_drop or hip_drop >= p.core_hip_drop)))
        if not stronger:
            episode.progression.clear()
            return None, False
        append_observed(episode.progression, o, p.fresh_tail_seconds, p.fresh_tail_frames)
        if (t - episode.started < 1 or len(episode.progression) < p.fresh_tail_frames
                or t - episode.progression[0]['t'] < p.fresh_tail_seconds - 1e-8):
            return None, False
        episode.progression_sent = True
        return Signal('suspected_fall', key,
                      min(ref['confidence'], *(a['confidence'] for a in episode.progression)), {
            'algorithm': 'observed_fall_progression_review_v1', 'review_required': True,
            'review_episode': f'{key}:{episode.number}', 'review_stage': 'progression',
            'candidate_reason': 'further_observed_descent_after_an_earlier_review_proposal',
            'initial_candidate_t': episode.started,
            'additional_shoulder_drop_normalized': round(shoulder_drop, 6),
            'additional_hip_drop_normalized': round(hip_drop, 6),
            'fresh_core_frames': len(episode.progression),
            'fresh_core_span_seconds': round(t - episode.progression[0]['t'], 6),
            'descent_observed': True, 'fall_transition_observed': False,
            'floor_contact_observed': False,
            'confidence_kind': 'minimum_observed_pose_and_core_scores_not_event_probability',
            'limitation': 'One bounded evidence update after further observed motion, not proof of a fall.'
        }), False

    def accept(self, signal, pose, t):
        key = signal.track_id
        if key in self.episodes:
            return None
        ref = self.latest.get(key)
        if ref is None:
            # Preserve an existing strict proposal even if extra recovery checks fail.
            return signal
        self.counter += 1
        episode = self.episodes[key] = Episode(self.counter, t, ref, last_t=t)
        return Signal(signal.event_type, key, signal.confidence, {
            **signal.details, 'review_episode': f'{key}:{episode.number}', 'review_stage': 'initial'})

    def merge_progression(self, key):
        """Evidence merged before dispatch does not consume the one follow-up."""
        episode = self.episodes.get(key)
        if episode and key in self.latest:
            episode.reference = self.latest[key]
            episode.progression.clear()
            episode.progression_sent = False

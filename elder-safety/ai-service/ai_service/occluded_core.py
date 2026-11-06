"""Review-only observed descent when lower-body joints cannot establish posture."""
from collections import deque
from dataclasses import dataclass, field
import math

import numpy as np

from .review_recovery import SeatPolicy, SlowReview, append_observed, observation
from .types import iou


@dataclass
class CoreContext:
    background: deque = field(default_factory=deque)
    tail: deque = field(default_factory=deque)
    reference: dict | None = None
    last_t: float | None = None
    last_box: tuple | None = None


class OccludedCoreReview(SlowReview):
    """A stable upper-body reference is not a seated/standing classification."""
    background_seconds = .5
    reference_seconds = 6.
    maximum_sample_gap = .2

    def __init__(self, config):
        super().__init__(config)
        self.core_contexts = {}

    def reset(self):
        super().reset()
        self.core_contexts.clear()

    def retain(self, present, *, recoverable=None):
        super().retain(present, recoverable=recoverable)
        # No reference or observed duration crosses a missing person sample.
        self.core_contexts = {k: v for k, v in self.core_contexts.items() if k in present}

    def update(self, key, pose, t, armed, *, recovery_allowed=True):
        parent = super().update(key, pose, t, armed, recovery_allowed=recovery_allowed)
        c = self.config
        p = SeatPolicy()
        o = observation(pose, self.frame_shape, c.keypoint_confidence, p) if recovery_allowed else None
        if o is None:
            self.core_contexts.pop(key, None)
            return parent
        k = np.asarray(pose.keypoints)
        h, w = self.frame_shape[:2]
        # A reliable foot still carries support/translation evidence; leave that
        # case to existing rules even if its knee is missing.
        foot_available = any(bool((k[ids, 2] >= c.keypoint_confidence).all())
                            and bool(((k[ids, 0] >= 0) & (k[ids, 0] <= w)).all())
                            and bool(((k[ids, 1] >= 0) & (k[ids, 1] <= h)).all())
                            for ids in ([15], [16]))
        o = {**o, 't': t, 'feet_unreliable': not foot_available}
        state = self.core_contexts.setdefault(key, CoreContext())
        if (state.last_t is not None and not 0 < t - state.last_t <= min(self.maximum_sample_gap, c.max_gap_seconds) + 1e-8
                or state.last_box is not None and iou(pose.bbox, state.last_box) < .3):
            state = self.core_contexts[key] = CoreContext()
        state.last_t, state.last_box = t, pose.bbox
        if state.reference is not None and t - state.reference['t'] > self.reference_seconds + 1e-8:
            state.reference = None
            state.tail.clear()
        torso = o['hip'] - o['shoulder']
        angle = math.degrees(math.atan2(abs(torso[0]), abs(torso[1])))
        if torso[1] > 0 and angle <= p.maximum_seated_torso_angle:
            append_observed(state.background, o, self.background_seconds)
            first = state.background[0]
            unknown = [a for a in state.background if a['feet_unreliable']]
            # Foot confidence may flicker while the same core is continuously
            # observed. Require repeated missing support evidence, not every foot.
            lower_body_unknown = (len(unknown) >= p.fresh_tail_frames
                                  and unknown[-1]['t'] - unknown[0]['t'] >= p.fresh_tail_seconds - 1e-8)
            stable = (len(state.background) >= 3 and t - first['t'] >= self.background_seconds - 1e-8
                      and all(max(np.linalg.norm(a['hip'] - first['hip']),
                                  np.linalg.norm(a['shoulder'] - first['shoulder'])) / first['height'] <= .06
                              for a in state.background))
            # Freeze the pre-descent anchor instead of following a slow slide.
            if stable and lower_body_unknown and state.reference is None:
                state.reference = o
        else:
            state.background.clear()
        ref = state.reference
        if ref is None:
            return parent
        shoulder_drop = float((o['shoulder'][1] - ref['shoulder'][1]) / ref['height'])
        hip_drop = float((o['hip'][1] - ref['hip'][1]) / ref['height'])
        if shoulder_drop >= p.core_shoulder_drop and hip_drop >= p.core_hip_drop:
            append_observed(state.tail, o, p.fresh_tail_seconds, p.fresh_tail_frames)
        else:
            state.tail.clear()
        if parent is not None:
            return parent
        if (len(state.tail) < p.fresh_tail_frames
                or t - state.tail[0]['t'] < p.fresh_tail_seconds - 1e-8):
            return None
        return self._publish(key, pose, t, min(ref['confidence'], *(a['confidence'] for a in state.tail)), {
            'algorithm': 'lower_body_unreliable_core_descent_review_v1', 'review_required': True,
            'candidate_reason': 'observed_core_descent_from_stable_background_with_unreliable_lower_body',
            'preceding_phase': 'stable_core_posture_unknown',
            'reference_t': round(ref['t'], 6), 'elapsed_seconds': round(t - ref['t'], 6),
            'shoulder_drop_normalized': round(shoulder_drop, 6), 'hip_drop_normalized': round(hip_drop, 6),
            'normalization': 'observed_reference_bbox_height_not_verified_full_body_height',
            'fresh_core_frames': len(state.tail), 'fresh_core_span_seconds': round(t - state.tail[0]['t'], 6),
            'lower_body_unreliable_in_background': True,
            'feet_unreliable_at_reference_t': ref['feet_unreliable'], 'occlusion_verified': False,
            'whole_body_required': False, 'standing_required': False, 'chair_verified': False,
            'fall_transition_observed': False, 'descent_observed': True, 'floor_contact_observed': False,
            'confidence_kind': 'minimum_observed_pose_and_core_scores_not_event_probability',
            'limitation': 'Lower-body reliability loss is not verified occlusion. Stable core posture and observed descent only; sitting, squatting, camera motion and pose errors may match. Visual review required; no support loss, chair or ground contact established.'
        })

"""Model-flame persistence candidates for visual review, never direct alarms.

Only call update for fresh model observations (including observed empty frames),
not cached overlays. Strict results should first pass accept_strict(box, t), then
update should consume that same timestamp. Both paths share a spatial cooldown.
A new stream/time origin requires reset; backward/invalid times discard evidence
but cannot erase an already issued region's cooldown.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math

from .config import FireConfig
from .types import Box, Flame, Signal, finite_box, iou


_MIN_HIT_RATE = 0.70
_MAX_CENTER_DISTANCE = 0.75  # Fraction of the smaller box diagonal.
_MAX_AREA_RATIO = 4.0


def _box(value) -> Box | None:
    try:
        if len(value) != 4:
            return None
        box = tuple(float(v) for v in value)
        if not finite_box(box):
            return None
        width, height = box[2] - box[0], box[3] - box[1]
        if not (math.isfinite(width * height) and width * height > 0
                and math.isfinite(math.hypot(width, height))):
            return None
        return box
    except (TypeError, ValueError, OverflowError):
        return None


def _match(a: Box, b: Box, region_iou: float) -> float | None:
    """Prefer IoU; allow a bounded, similar-size center move for tiny flames."""
    overlap = iou(a, b)
    if overlap > 0 and overlap >= region_iou:
        return 1.0 + overlap
    aw, ah = a[2] - a[0], a[3] - a[1]
    bw, bh = b[2] - b[0], b[3] - b[1]
    area_a, area_b = aw * ah, bw * bh
    if max(area_a, area_b) / min(area_a, area_b) > _MAX_AREA_RATIO:
        return None
    dx = (a[0] / 2 + a[2] / 2) - (b[0] / 2 + b[2] / 2)
    dy = (a[1] / 2 + a[3] / 2) - (b[1] / 2 + b[3] / 2)
    distance = math.hypot(dx, dy) / min(math.hypot(aw, ah), math.hypot(bw, bh))
    return 1.0 - distance if distance <= _MAX_CENTER_DISTANCE else None


@dataclass
class _State:
    box: Box
    since: float
    last_t: float  # Last matched model observation, never a missing frame.
    count: int = 0
    observations: int = 0
    recent_hits: deque = field(default_factory=lambda: deque(maxlen=64))
    alert: _Alert | None = None


@dataclass
class _Alert:
    box: Box
    t: float


class ReviewFireDetector:
    """Independent review-only detector using the existing FireConfig bounds.

    states[signal.track_id].box is the latest matched box for a collector hint.
    Duplicate/invalid/backward update times clear persistence
    and are rejected; the monotonic watermark and cooldown survive until reset.
    Misses count against both lifetime and recent (last 64 observations) hit rate.
    """

    def __init__(self, config: FireConfig):
        self.config = config
        self.states: dict[str, _State] = {}
        self.next_id = 1
        self.recent_alerts: deque[_Alert] = deque()
        self._clock: float | None = None
        self._last_update_t: float | None = None

    def reset(self) -> None:
        self.states.clear()
        self.recent_alerts.clear()
        self._clock = None
        self._last_update_t = None

    def _advance(self, t: float) -> bool:
        try:
            valid = not isinstance(t, bool) and math.isfinite(t)
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid or (self._clock is not None and t < self._clock):
            self.states.clear()
            return False
        self._clock = t
        while self.recent_alerts and t - self.recent_alerts[0].t >= self.config.cooldown_seconds:
            self.recent_alerts.popleft()
        return True

    def _matching_alert(self, box: Box) -> _Alert | None:
        latest = None
        for alert in self.recent_alerts:
            if (_match(alert.box, box, self.config.region_iou) is not None
                    and (latest is None or alert.t > latest.t)):
                latest = alert
        return latest

    def _accept_region(self, box: Box, t: float) -> bool:
        if self._matching_alert(box) is not None:
            return False
        alert = _Alert(box, t)
        self.recent_alerts.append(alert)
        for state in self.states.values():
            if _match(state.box, box, self.config.region_iou) is not None:
                state.alert = alert
        return True

    def accept_strict(self, box: Box, t: float) -> bool:
        """Gate an already qualified strict result; True reserves its cooldown.

        Does not create tracks or add hits. Call before update at the same model
        timestamp; call reset on a stream/session change, never between paths.
        """
        if not self._advance(t):
            return False
        valid_box = _box(box)
        return valid_box is not None and self._accept_region(valid_box, t)

    def update(self, detections: list[Flame], t: float) -> list[Signal]:
        if not self._advance(t):
            return []
        if t == self._last_update_t:
            self.states.clear()
            return []
        self._last_update_t = t
        c = self.config
        threshold = max(0.25, c.confidence * 0.65)
        valid = []
        for detection in detections:
            box = _box(detection.bbox)
            try:
                score_ok = (not isinstance(detection.confidence, bool)
                            and math.isfinite(detection.confidence)
                            and threshold <= detection.confidence <= 1.0)
            except (TypeError, ValueError, OverflowError):
                score_ok = False
            if box is not None and score_ok:
                valid.append(Flame(box, float(detection.confidence), detection.label))

        self.states = {key: state for key, state in self.states.items()
                       if t - state.last_t <= c.max_gap_seconds + 1e-8}
        # Compute all edges before mutating boxes. Each existing region and each
        # detection may be used once; duplicate boxes cannot add multiple hits.
        edges = []
        for key, state in self.states.items():
            for index, detection in enumerate(valid):
                score = _match(state.box, detection.bbox, c.region_iou)
                if score is not None:
                    edges.append((score, detection.confidence, key, index))
        assigned = {}
        used = set()
        for _, _, key, index in sorted(edges, reverse=True):
            if key not in assigned and index not in used:
                assigned[key] = index
                used.add(index)
        for index in sorted(set(range(len(valid))) - used,
                            key=lambda i: (-valid[i].confidence, i)):
            key = f"review-fire-{self.next_id}"
            self.next_id += 1
            self.states[key] = _State(valid[index].bbox, t, t)
            assigned[key] = index

        # Unmatched tracks survive only max_gap from their last hit. An invalid
        # detection is a miss, not fresh evidence and not an extension of the gap.
        signals = []
        for key, state in self.states.items():
            hit = key in assigned
            state.observations += 1
            state.recent_hits.append(hit)
            if not hit:
                continue
            detection = valid[assigned[key]]
            state.box = detection.bbox
            state.last_t = t
            state.count += 1
            # A shared alert follows its live track, so drift, expiry or an
            # invalid next observation cannot erase that region's cooldown.
            if state.alert is not None and t - state.alert.t < c.cooldown_seconds:
                state.alert.box = state.box
            else:
                state.alert = self._matching_alert(state.box)
            hit_rate = state.count / state.observations
            recent_hit_rate = sum(state.recent_hits) / len(state.recent_hits)
            if (state.count < c.min_frames or t - state.since + 1e-8 < c.hold_seconds
                    or min(hit_rate, recent_hit_rate) < _MIN_HIT_RATE
                    or not self._accept_region(state.box, t)):
                continue
            signals.append(Signal("fire_detected", key, detection.confidence, {
                "algorithm": "review_persistent_flame_region_v1",
                "review_required": True,
                "candidate_reason": "persistent_model_flame_for_visual_review",
                "label": detection.label,
                "frames": state.count,
                "observations": state.observations,
                "missing_observations": state.observations - state.count,
                "hold_seconds": round(t - state.since, 3),
                "hit_rate": round(hit_rate, 6),
                "recent_hit_rate": round(recent_hit_rate, 6),
                "min_hit_rate": _MIN_HIT_RATE,
                "confidence_threshold": threshold,
                "max_gap_seconds": c.max_gap_seconds,
                "max_center_distance_relative": _MAX_CENTER_DISTANCE,
                "confidence_kind": "flame_detector_score_not_calibrated_event_probability",
                "limitation": "Model-flame candidate only; TV imagery and lamps require visual review",
            }))
        return signals

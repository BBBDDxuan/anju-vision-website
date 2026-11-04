from __future__ import annotations

from .config import Config
from .temporal import FallDetector, FireDetector
from .help_gesture import HelpGestureDetector
from .help_candidates import ReviewHelpDetector
from .fire_candidates import ReviewFireDetector
from .fall_candidates import ReviewFallDetector
from .tracking import Tracker
from .types import Sample, iou


class DetectionEngine:
    def __init__(self, config: Config, *, allow_review_candidates: bool = False):
        self.tracker = Tracker(config.tracking)
        self.fall = FallDetector(config.fall)
        self.review_fall = ReviewFallDetector(config.fall) if allow_review_candidates else None
        self.fire = FireDetector(config.fire)
        self.review_fire = ReviewFireDetector(config.fire) if allow_review_candidates else None
        self.help = HelpGestureDetector(config.help)
        self.review_help = ReviewHelpDetector(config.help) if allow_review_candidates else None
        self.session = None
        self.last_t = None
        self.max_gap = min(config.fall.max_gap_seconds, config.fire.max_gap_seconds,
                           config.tracking.max_gap_seconds, config.help.max_gap_seconds)

    def discontinuous(self, sample):
        return (self.session is not None and (sample.session != self.session
                or sample.t <= self.last_t or sample.t-self.last_t > self.max_gap))

    def process(self, sample: Sample):
        if self.discontinuous(sample):
            self.tracker.clear()
            self.fall.reset()
            if self.review_fall:
                self.review_fall.reset()
            self.fire.reset()
            if self.review_fire:
                self.review_fire.reset()
            self.help.reset()
            if self.review_help:
                self.review_help.reset()
        self.session = sample.session
        self.last_t = sample.t
        assigned = self.tracker.update([p.bbox for p in sample.poses], sample.t)
        self.fall.retain(set(assigned.values()))
        if self.review_fall:
            self.review_fall.retain(set(assigned.values()), recoverable=set(self.tracker.tracks))
        self.help.retain(set(assigned.values()))
        if self.review_help:
            self.review_help.retain(set(assigned.values()))
        signals = []
        for index, track_id in assigned.items():
            result = self.fall.update(track_id, sample.poses[index], sample.t)
            if self.review_fall:
                if result:
                    if not self.review_fall.accept_strict(track_id, sample.t, sample.poses[index].bbox):
                        result = None
                else:
                    state = self.fall.states.get(track_id)
                    result = self.review_fall.update(track_id, sample.poses[index], sample.t,
                                                     bool(state and state.armed_at is not None),
                                                     recovery_allowed=not any(
                                                         other != index and iou(sample.poses[index].bbox, p.bbox) >= 0.3
                                                         for other, p in enumerate(sample.poses)))
            if result:
                signals.append(result)
            help_result = self.help.update(track_id, sample.poses[index], sample.t)
            if self.review_help:
                if help_result and not self.review_help.accept_strict(track_id, sample.t):
                    help_result = None
                review_result = self.review_help.update(track_id, sample.poses[index], sample.t)
                help_result = help_result or review_result
            if help_result:
                signals.append(help_result)
        if sample.flames_observed:
            flame_t = sample.t if sample.flame_t is None else sample.flame_t
            if 0 <= sample.t - flame_t <= self.fire.config.max_gap_seconds:
                fire_results = self.fire.update(sample.flames, flame_t)
                if self.review_fire:
                    fire_results = [s for s in fire_results if self.review_fire.accept_strict(
                        self.fire.states[s.track_id].box, flame_t)]
                    fire_results.extend(self.review_fire.update(sample.flames, flame_t))
                signals.extend(fire_results)
        return signals

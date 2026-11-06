"""Local-only small-model candidates; no recording, delivery or model calls.

Evidence uses a 4fps ring. Normal candidates use [trigger - 2, trigger + 1);
arm-motion proposals use three pre-seconds, posture recovery four and slow descent six.
Early seated/progression proposals collect until motion settles, at most five
post-seconds. All requests still contain at most twelve real frames.
Only real sampled frames are retained; sparse input is never padded. ``reset``
discards pending evidence, so call ``finish`` first if it should be preserved.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import time
import uuid

import cv2
import numpy as np

from .config import Config
from .engine import DetectionEngine
from .types import Sample


EVENT_TYPES = frozenset({"suspected_fall", "help_requested", "fire_detected"})
_INTERVAL = 0.25
_PRE_SECONDS = 2.0
_HELP_MOTION_PRE_SECONDS = 3.0
_RECOVERY_PRE_SECONDS = 4.0
_SLOW_PRE_SECONDS = 6.0
_POST_SECONDS = 1.0
_MAX_ADAPTIVE_POST_SECONDS = 5.0
_SETTLE_SECONDS = .75
_MAX_FRAMES = 12
_MAX_PENDING = 8
_EPS = 1e-8
_CORE_FALL_ALGORITHM = 'lower_body_unreliable_core_descent_review_v1'


def _atomic_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    with temporary.open("xb") as stream:
        if stream.write(content) != len(content):
            raise OSError("Incomplete safety evidence write")
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            break
        except PermissionError as exc:
            # On Windows a concurrent snapshot reader can briefly deny rename.
            # Retry the same flushed bytes; keep the old commit on real failure.
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                raise
            time.sleep(.02 * (attempt + 1))


def atomic_json(path: Path, value: dict) -> None:
    """Publish strict JSON only after a complete, flushed local write."""
    content = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    _atomic_bytes(path, content.encode("utf-8"))


@dataclass(frozen=True)
class _Frame:
    id: str
    t: float
    jpeg: bytes
    people: dict = field(default_factory=dict)


@dataclass
class _Pending:
    candidate: dict
    end: float
    frames: list[_Frame]
    adaptive: bool = False
    last_motion_t: float = 0.0
    last_core: dict | None = None


class SafetyCandidateCollector:
    def __init__(self, config: Config, output: Path):
        self.engine = DetectionEngine(config, allow_review_candidates=True)
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        # Slow descent needs more prehistory; request size remains twelve frames.
        self._ring: deque[_Frame] = deque(maxlen=25)
        self._pending: list[_Pending] = []
        self._next_frame_t: float | None = None

    def reset(self) -> None:
        """Clear stream state, preserving the engine object used for diagnostics."""
        self._ring.clear()
        self._pending.clear()
        self._next_frame_t = None
        self.engine.tracker.clear()
        self.engine.fall.reset()
        if self.engine.review_fall:
            self.engine.review_fall.reset()
            self.engine.review_lifecycle.reset()
        self.engine.fire.reset()
        if self.engine.review_fire:
            self.engine.review_fire.reset()
        self.engine.help.reset()
        if self.engine.review_help:
            self.engine.review_help.reset()
        self.engine.session = None
        self.engine.last_t = None

    @staticmethod
    def _frame(sample: Sample) -> _Frame:
        image = sample.image
        height, width = image.shape[:2]
        if not height or not width:
            raise ValueError("Empty safety evidence frame")
        scale = min(1.0, 960 / max(height, width))
        if scale < 1.0:
            image = cv2.resize(image, (max(1, round(width * scale)),
                                       max(1, round(height * scale))),
                               interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok or encoded is None or not encoded.size:
            raise OSError("Cannot encode safety evidence JPEG")
        return _Frame("sf_" + uuid.uuid4().hex, float(sample.t), encoded.tobytes())

    def _selected_frames(self, pending: _Pending) -> list[_Frame]:
        candidate = dict(pending.candidate)
        selected = pending.frames
        if pending.adaptive:
            before = [f for f in selected if f.t < candidate['t'] - _EPS]
            after = [f for f in selected if f.t >= candidate['t'] - _EPS]
            def spread(values, count):
                return values if len(values) <= count else [values[round(i*(len(values)-1)/(count-1))] for i in range(count)]
            selected = spread(before, 6) + spread(after, 6)
            pending.candidate['details'] = {**candidate['details'], 'evidence_post_seconds':
                round(max((f.t - candidate['t'] for f in selected), default=0), 6),
                'evidence_collection': 'motion_settled_or_five_second_limit'}
        return selected

    def _save(self, pending: _Pending) -> dict:
        selected = self._selected_frames(pending)
        candidate = dict(pending.candidate)
        directory = self.output / candidate["id"]
        directory.mkdir(exist_ok=True)
        frames = []
        for frame in selected:
            path = directory / (frame.id + ".jpg")
            _atomic_bytes(path, frame.jpeg)
            frames.append({"id": frame.id, "pts_ms": round(frame.t * 1000),
                           "path": str(path), "person_bboxes": frame.people})
        candidate["frames"] = frames
        # A manifest is the commit marker. Partial JPEG writes are not candidates.
        atomic_json(directory / "manifest.json", candidate)
        return candidate

    def finish(self) -> list[dict]:
        """Flush available real frames, including candidates with <1s of post-roll."""
        completed = []
        while self._pending:
            completed.append(self._save(self._pending[0]))
            self._pending.pop(0)
        return completed

    def process(self, sample: Sample) -> list[dict]:
        t = float(sample.t)
        if not math.isfinite(t) or not math.isfinite(t * 1000):
            raise ValueError("Safety sample time must be finite")
        completed = []
        # Mirror the engine's session, rewind and gap boundary, without bridging
        # pre/post evidence from two streams. Explicit reset instead discards it.
        if self.engine.discontinuous(sample):
            completed.extend(self.finish())
            self.reset()
        while self._ring and self._ring[0].t < t - _SLOW_PRE_SECONDS - _EPS:
            self._ring.popleft()
        frame = None
        if self._next_frame_t is None:
            self._next_frame_t = t
        if t >= self._next_frame_t - _EPS:
            frame = self._frame(sample)
            # Advance the sampling clock, not t + interval: 10/30fps input
            # must not drift down to 3.33fps. Never synthesize missed ticks.
            ticks = max(1, math.floor((t - self._next_frame_t + _EPS) / _INTERVAL) + 1)
            self._next_frame_t += ticks * _INTERVAL
            self._ring.append(frame)
        signals = self.engine.process(sample)
        for pending in list(self._pending):
            core = self.engine.review_lifecycle.latest.get(pending.candidate['track_id'])
            if pending.adaptive and core is not None:
                previous = pending.last_core
                if previous is None or max(np.linalg.norm(core['hip'] - previous['hip']),
                        np.linalg.norm(core['shoulder'] - previous['shoulder'])) / previous['height'] >= .01:
                    pending.last_motion_t = t
                    pending.last_core = core
            deadline = pending.candidate['t'] + _MAX_ADAPTIVE_POST_SECONDS
            settled = core is not None and t - pending.last_motion_t >= _SETTLE_SECONDS - _EPS
            ready = (t >= deadline - _EPS or (t >= pending.end - _EPS and settled)) if pending.adaptive else t >= pending.end - _EPS
            if not ready and frame is not None and (pending.adaptive or len(pending.frames) < _MAX_FRAMES):
                pending.frames.append(frame)
            if ready:
                completed.append(self._save(pending))
                self._pending.remove(pending)

        # Detection always runs at source cadence on this single engine. Evidence
        # sampling never changes detector thresholds, inputs or configuration.
        if frame is not None:
            height, width = sample.image.shape[:2]
            # Freeze only currently observed tracks, not stale predictions. Boxes
            # are normalized so they remain valid after evidence JPEG resizing.
            for track_id, track in self.engine.tracker.tracks.items():
                if track.last_t == sample.t:
                    box = [max(0., min(1., float(value) / size))
                           for value, size in zip(track.box, (width, height, width, height))]
                    if box[0] < box[2] and box[1] < box[3]:
                        frame.people[track_id] = box
        for signal in signals:
            if signal.event_type not in EVENT_TYPES or len(self._pending) >= _MAX_PENDING:
                continue
            episode = signal.details.get('review_episode')
            existing = next((p for p in self._pending if episode and
                p.candidate['event_type'] == signal.event_type and
                p.candidate['track_id'] == signal.track_id and
                p.candidate['details'].get('review_episode') == episode), None)
            if existing is not None:
                existing.candidate['details']['progression_observed_at'] = t
                existing.candidate['details']['progression_evidence'] = signal.details
                if existing.candidate['details'].get('review_stage') == 'initial':
                    self.engine.review_lifecycle.merge_progression(signal.track_id)
                continue
            confidence = float(signal.confidence)
            if not math.isfinite(confidence):
                raise ValueError("Safety confidence must be finite")
            if not isinstance(signal.track_id, str) or not isinstance(signal.details, dict):
                raise ValueError("Invalid safety signal metadata")
            # Freeze metadata and reject non-finite or non-JSON values before I/O.
            details = json.loads(json.dumps(signal.details, allow_nan=False))
            candidate = {"id": "sc_" + uuid.uuid4().hex,
                         "event_type": signal.event_type, "track_id": signal.track_id,
                         "confidence": confidence, "details": details, "t": t,
                         "occurred_at": sample.occurred_at.isoformat()}
            # Trigger-time subject hint only; the original signal details stay unchanged.
            track = (self.engine.fire.states.get(signal.track_id) if signal.event_type == "fire_detected"
                     else self.engine.tracker.tracks.get(signal.track_id))
            if track is None and signal.event_type == "fire_detected" and self.engine.review_fire:
                track = self.engine.review_fire.states.get(signal.track_id)
            box = getattr(track, "box", None)
            if box is not None:
                height, width = sample.image.shape[:2]
                candidate["subject_bbox_hint"] = [float(box[0])/width, float(box[1])/height,
                                                   float(box[2])/width, float(box[3])/height]
            recovery = signal.event_type == "suspected_fall" and details.get("fall_transition_observed") is False
            slow = (signal.event_type == "suspected_fall"
                    and details.get("algorithm") in {"slow_descent_low_posture_review_v1", _CORE_FALL_ALGORITHM})
            motion_help = (signal.event_type == "help_requested" and
                           details.get("algorithm") == "body_relative_repeated_arm_motion_review_v2")
            pre = (_SLOW_PRE_SECONDS if slow else _RECOVERY_PRE_SECONDS if recovery
                   else _HELP_MOTION_PRE_SECONDS if motion_help else _PRE_SECONDS)
            history = [f for f in self._ring if t - pre - _EPS <= f.t < t - _EPS]
            if (recovery or slow or motion_help) and len(history) > 8:
                # Include both oldest and newest pre-frames; no fabricated ticks.
                history = [history[round(i * (len(history) - 1) / 7)] for i in range(8)]
            else:
                history = history[-8:]
            if frame is not None:
                history.append(frame)
            adaptive = (signal.event_type == 'suspected_fall' and details.get('algorithm') in {
                'seated_core_descent_review_v1', 'observed_fall_progression_review_v1', _CORE_FALL_ALGORITHM})
            self._pending.append(_Pending(candidate, t + _POST_SECONDS, history, adaptive, t,
                self.engine.review_lifecycle.latest.get(signal.track_id)))
        return completed

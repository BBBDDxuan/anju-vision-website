"""Local-only small-model candidates; no recording, delivery or model calls.

Evidence uses a 4fps ring. Normal candidates use [trigger - 2, trigger + 1);
posture-recovery candidates sample [trigger - 4, trigger + 1), still at most 12 frames.
Only real sampled frames are retained; sparse input is never padded. ``reset``
discards pending evidence, so call ``finish`` first if it should be preserved.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import uuid

import cv2

from .config import Config
from .engine import DetectionEngine
from .types import Sample


EVENT_TYPES = frozenset({"suspected_fall", "help_requested", "fire_detected"})
_INTERVAL = 0.25
_PRE_SECONDS = 2.0
_RECOVERY_PRE_SECONDS = 4.0
_POST_SECONDS = 1.0
_MAX_FRAMES = 12
_MAX_PENDING = 8
_EPS = 1e-8


def _atomic_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    with temporary.open("xb") as stream:
        if stream.write(content) != len(content):
            raise OSError("Incomplete safety evidence write")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict) -> None:
    """Publish strict JSON only after a complete, flushed local write."""
    content = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    _atomic_bytes(path, content.encode("utf-8"))


@dataclass(frozen=True)
class _Frame:
    id: str
    t: float
    jpeg: bytes


@dataclass
class _Pending:
    candidate: dict
    end: float
    frames: list[_Frame]


class SafetyCandidateCollector:
    def __init__(self, config: Config, output: Path):
        self.engine = DetectionEngine(config, allow_review_candidates=True)
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        # Keep up to four seconds so delayed posture recovery can include the
        # lost descent. Each candidate still sends at most twelve real frames.
        self._ring: deque[_Frame] = deque(maxlen=17)
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
        self.engine.fire.reset()
        self.engine.help.reset()
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

    def _save(self, pending: _Pending) -> dict:
        candidate = dict(pending.candidate)
        directory = self.output / candidate["id"]
        directory.mkdir(exist_ok=True)
        frames = []
        for frame in pending.frames:
            path = directory / (frame.id + ".jpg")
            _atomic_bytes(path, frame.jpeg)
            frames.append({"id": frame.id, "pts_ms": round(frame.t * 1000),
                           "path": str(path)})
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
        while self._ring and self._ring[0].t < t - _RECOVERY_PRE_SECONDS - _EPS:
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
        for pending in list(self._pending):
            if t >= pending.end - _EPS:
                completed.append(self._save(pending))
                self._pending.remove(pending)
            elif frame is not None and len(pending.frames) < _MAX_FRAMES:
                pending.frames.append(frame)

        # Detection always runs at source cadence on this single engine. Evidence
        # sampling never changes detector thresholds, inputs or configuration.
        for signal in self.engine.process(sample):
            if signal.event_type not in EVENT_TYPES or len(self._pending) >= _MAX_PENDING:
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
            box = getattr(track, "box", None)
            if box is not None:
                height, width = sample.image.shape[:2]
                candidate["subject_bbox_hint"] = [float(box[0])/width, float(box[1])/height,
                                                   float(box[2])/width, float(box[3])/height]
            recovery = signal.event_type == "suspected_fall" and details.get("fall_transition_observed") is False
            pre = _RECOVERY_PRE_SECONDS if recovery else _PRE_SECONDS
            history = [f for f in self._ring if t - pre - _EPS <= f.t < t - _EPS]
            if recovery and len(history) > 8:
                # Include both oldest and newest pre-frames; no fabricated ticks.
                history = [history[round(i * (len(history) - 1) / 7)] for i in range(8)]
            else:
                history = history[-8:]
            if frame is not None:
                history.append(frame)
            self._pending.append(_Pending(candidate, t + _POST_SECONDS, history))
        return completed

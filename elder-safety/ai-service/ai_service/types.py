from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math

import numpy as np

Box = tuple[float, float, float, float]


def iou(a: Box, b: Box) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    union += max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Pose:
    bbox: Box
    # COCO order, shape (17, 3): x/y pixels, confidence.
    keypoints: np.ndarray
    confidence: float = 1.0


@dataclass
class Flame:
    bbox: Box
    confidence: float
    label: str = "flame"


@dataclass
class Sample:
    image: np.ndarray
    t: float  # monotonic stream time in seconds, never wall-clock deltas
    occurred_at: datetime
    session: int = 0
    poses: list[Pose] = field(default_factory=list)
    flames: list[Flame] = field(default_factory=list)
    simulated: bool = False
    # Async fire results are observations only once; cached overlays never count.
    flames_observed: bool = True
    flame_t: float | None = None


@dataclass
class Signal:
    event_type: str
    track_id: str
    confidence: float
    details: dict


def finite_box(box: Box) -> bool:
    return (len(box) == 4 and all(math.isfinite(float(v)) for v in box)
            and box[2] > box[0] and box[3] > box[1])

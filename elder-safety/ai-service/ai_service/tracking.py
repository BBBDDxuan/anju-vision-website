from __future__ import annotations

from dataclasses import dataclass
import math

from .config import TrackingConfig
from .types import Box, finite_box, iou


@dataclass
class Track:
    box: Box
    last_t: float


class Tracker:
    """Greedy one-to-one IoU/center matching. Expired IDs are never reused."""

    def __init__(self, config: TrackingConfig, prefix: str = "person"):
        self.config = config
        self.prefix = prefix
        self.next_id = 1
        self.tracks: dict[str, Track] = {}

    def clear(self):
        self.tracks.clear()

    def update(self, boxes: list[Box], t: float) -> dict[int, str]:
        c = self.config
        self.tracks = {k: v for k, v in self.tracks.items()
                       if 0 <= t - v.last_t <= c.max_gap_seconds}
        candidates = []
        valid = [i for i, box in enumerate(boxes) if finite_box(box)]
        for i in valid:
            box = boxes[i]
            for key, track in self.tracks.items():
                old = track.box
                overlap = iou(box, old)
                distance = math.hypot((box[0]+box[2]-old[0]-old[2])/2,
                                      (box[1]+box[3]-old[1]-old[3])/2)
                scale = max(old[2]-old[0], old[3]-old[1], 1)
                if overlap >= c.min_iou and distance / scale <= c.max_center_distance:
                    candidates.append((overlap, i, key))
        assigned: dict[int, str] = {}
        used = set()
        for _, i, key in sorted(candidates, reverse=True):
            if i not in assigned and key not in used:
                assigned[i] = key
                used.add(key)
        for i in valid:
            if i not in assigned:
                assigned[i] = f"{self.prefix}-{self.next_id}"
                self.next_id += 1
            self.tracks[assigned[i]] = Track(boxes[i], t)
        return assigned

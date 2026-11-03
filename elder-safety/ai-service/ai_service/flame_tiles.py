"""Bounded overlapping views with original-frame coordinates; no color heuristic."""
from __future__ import annotations
import math

from .types import Flame, finite_box, iou


def starts(length: int, side: int, overlap: float):
    if length <= side:
        return [0]
    stride = max(1, int(side * (1-overlap)))
    return sorted(set(range(0, length-side+1, stride)) | {length-side})


def flame_views(image, side: int, overlap: float, max_tiles: int):
    """Increase tile size if needed to cover the full frame within the budget."""
    h, w = image.shape[:2]
    if max(h, w) <= side:
        return []
    while True:
        xs, ys = starts(w, side, overlap), starts(h, side, overlap)
        if len(xs)*len(ys) <= max_tiles:
            break
        side = max(side+1, math.ceil(side*1.25))
    if side >= max(h, w):
        return []  # Already covered by full-frame inference.
    return [(image[y:min(y+side,h), x:min(x+side,w)], x, y) for y in ys for x in xs]


def merge_flames(detections: list[Flame], threshold=0.45):
    """Remove duplicate full-frame/tile boxes before temporal persistence counting."""
    kept = []
    for detection in sorted(detections, key=lambda d: d.confidence, reverse=True):
        if not finite_box(detection.bbox) or not math.isfinite(detection.confidence):
            continue
        if any(other.label == detection.label and iou(other.bbox, detection.bbox) >= threshold for other in kept):
            continue
        kept.append(detection)
    return kept

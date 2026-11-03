"""Bounded, latest-frame fire inference, independent of the pose/preview loop."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class FireObservation:
    session: int
    t: float
    flames: list
    inference_ms: float


class LatestFireWorker:
    """One active job, one replaceable pending image, one consumable result.

    Images must remain immutable. The worker alone calls infer_flames; the main
    thread alone calls infer_pose. A cached result is never a new observation.
    """
    def __init__(self, infer_flames, interval_seconds=0.2):
        self.infer = infer_flames
        self.interval = interval_seconds
        self.condition = threading.Condition()
        self.pending = self.result = None
        self.stopping = False
        self.failed = False
        self.thread = threading.Thread(target=self._run, name="fire-inference", daemon=True)
        self.thread.start()

    def submit(self, image, t, session):
        with self.condition:
            if not self.stopping:
                self.pending = (image, t, session)
                self.condition.notify_all()

    def take(self, session, now, max_age):
        with self.condition:
            result, self.result = self.result, None
        if result is not None and result.session == session and 0 <= now-result.t <= max_age:
            return result
        return None

    def _run(self):
        next_start = 0.0
        while True:
            with self.condition:
                while not self.stopping:
                    delay = max(0, next_start-time.perf_counter())
                    if self.pending is not None and delay == 0:
                        break
                    self.condition.wait(delay if self.pending is not None else None)
                if self.stopping:
                    return
                image, t, session = self.pending
                self.pending = None
            started = time.perf_counter()
            try:
                flames = self.infer(image)
                result = FireObservation(session, t, flames, (time.perf_counter()-started)*1000)
                with self.condition:
                    self.result = result
                    self.failed = False
            except Exception:
                # Do not expose native messages which could contain private paths/URLs.
                with self.condition:
                    self.result = None
                    self.failed = True
            next_start = started+self.interval

    def close(self):
        with self.condition:
            self.stopping = True
            self.pending = self.result = None
            self.condition.notify_all()
        self.thread.join(timeout=2)

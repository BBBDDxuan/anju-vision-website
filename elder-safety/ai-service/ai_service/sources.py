from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import multiprocessing as mp
import os
from pathlib import Path
import threading
import time

import cv2
import numpy as np

from .config import Config
from .types import Flame, Pose, Sample


class SourceError(RuntimeError):
    pass


class SourceHealth:
    """Wall-clock freshness of successfully inferred frames, across threads."""

    def __init__(self, stale_seconds: float, clock=time.monotonic):
        self.stale_seconds, self.clock = stale_seconds, clock
        self.last_frame = None
        self.lock = threading.Lock()

    def mark_frame(self):
        with self.lock:
            self.last_frame = self.clock()

    def clear(self):
        with self.lock:
            self.last_frame = None

    def alive(self):
        with self.lock:
            return self.last_frame is not None and 0 <= self.clock()-self.last_frame <= self.stale_seconds


def mock_pose(progress: float = 0.0, hip_y: float | None = None) -> Pose:
    """Deterministic COCO landmarks, upright -> rapid drop -> prone."""
    p = min(1.0, max(0.0, progress))
    angle = math.radians(80*p)
    hip = np.array([270.0+50*p, 260.0+100*p if hip_y is None else hip_y])
    shoulder = hip - np.array([100*math.sin(angle), 100*math.cos(angle)])
    kp = np.zeros((17, 3), dtype=float)
    for left, right, center in ((5, 6, shoulder), (11, 12, hip)):
        kp[left] = [center[0]-10, center[1], 0.98]
        kp[right] = [center[0]+10, center[1], 0.98]
    kp[0] = [shoulder[0], shoulder[1]-20, 0.98]
    for index in (7, 8, 9, 10):
        kp[index] = [shoulder[0]+(index-8)*8, shoulder[1]+25, 0.98]
    for index in (13, 14, 15, 16):
        kp[index] = [hip[0]+(index-13)*10, hip[1]+20+(index-13)*5, 0.98]
    box = (230.0-55*p, 130.0+170*p, 330.0+65*p, 400.0)
    return Pose(box, kp, 0.98)


def mock_samples(stop: threading.Event, fps=10.0, max_frames=100, realtime=True):
    origin = datetime.now(timezone.utc)
    start = time.monotonic()
    for index in range(max_frames if max_frames is not None else 100):
        t = index/fps
        if realtime and stop.wait(max(0, start+t-time.monotonic())):
            return
        if stop.is_set():
            return
        image = np.full((480, 640, 3), (30, 30, 30), dtype=np.uint8)
        pose = mock_pose(min(1.0, max(0, (t-2.0)/0.4)))
        flames = [Flame((460.0, 220.0, 540.0, 340.0), 0.96)] if 4.0 <= t <= 7.0 else []
        box = tuple(map(int, pose.bbox))
        cv2.rectangle(image, box[:2], box[2:], (60, 220, 60), 2)
        for kp in pose.keypoints:
            if kp[2] > 0:
                cv2.circle(image, tuple(kp[:2].astype(int)), 4, (255, 220, 0), -1)
        for flame in flames:
            b = tuple(map(int, flame.bbox))
            cv2.rectangle(image, b[:2], b[2:], (0, 80, 255), -1)
        cv2.putText(image, "SIMULATION / SYNTHETIC - NOT REAL DETECTION", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 230, 255), 2)
        cv2.putText(image, f"synthetic time {t:.1f}s", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        yield Sample(image, t, origin+timedelta(seconds=t), poses=[pose], flames=flames, simulated=True)


def file_samples(path: str, stop: threading.Event, max_frames=None, realtime=True):
    target = Path(path)
    if not target.is_file():
        raise SourceError("video file does not exist")
    capture = cv2.VideoCapture(str(target))
    try:
        if not capture.isOpened():
            raise SourceError("cannot open video file")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or not 0 < fps <= 240:
            raise SourceError("video FPS unavailable or unsupported")
        origin = datetime.now(timezone.utc)
        start = time.monotonic()
        index = 0
        while not stop.is_set() and (max_frames is None or index < max_frames):
            ok, image = capture.read()
            if not ok:
                break  # file EOF; no reconnect loop
            t = index/fps
            if realtime and stop.wait(max(0, start+t-time.monotonic())):
                break
            yield Sample(image, t, origin+timedelta(seconds=t))
            index += 1
    finally:
        capture.release()


@dataclass(frozen=True)
class CapturedFrame:
    """Pickleable decoded frame; timestamps are taken in the capture thread."""

    image: np.ndarray
    t: float
    occurred_at: datetime
    sequence: int


class _LatestCapture:
    """One pending frame, with decoding independent of consumer/IPC latency."""

    def __init__(self, capture):
        self.capture = capture
        self.condition = threading.Condition()
        self.latest = None
        self.failed = False
        self.stopping = False
        self.thread = threading.Thread(target=self._run, name="rtsp-capture", daemon=True)
        self.thread.start()

    def _run(self):
        sequence = 0
        try:
            while True:
                with self.condition:
                    if self.stopping:
                        return
                ok, image = self.capture.read()
                if not ok or image is None:
                    return
                # These describe decode completion, never the later IPC request or
                # inference time. The camera's exposure time is not available here.
                # Python 3.11 on Windows uses coarse GetTickCount64 for monotonic.
                # perf_counter uses a high-resolution, process-shared clock.
                captured_t = time.perf_counter()
                occurred_at = datetime.now(timezone.utc)
                sequence += 1
                frame = CapturedFrame(image, captured_t, occurred_at, sequence)
                with self.condition:
                    if self.stopping:
                        return
                    self.latest = frame
                    self.condition.notify_all()
        except (Exception, KeyboardInterrupt):
            pass  # native/backend error strings may contain the camera credentials
        finally:
            with self.condition:
                self.failed = True
                self.latest = None  # never drain buffered frames after a disconnect
                self.condition.notify_all()
            try:
                self.capture.release()
            except Exception:
                pass

    def read(self, timeout: float):
        with self.condition:
            self.condition.wait_for(
                lambda: self.latest is not None or self.failed or self.stopping, timeout)
            if self.failed or self.stopping or self.latest is None:
                return "failed", None
            frame, self.latest = self.latest, None
            # A stalled decoder must not serve an arbitrarily old pending frame.
            if time.perf_counter() - frame.t > timeout:
                return "failed", None
            return "frame", frame

    def close(self):
        with self.condition:
            self.stopping = True
            self.latest = None
            self.condition.notify_all()
        # Only the reader calls release(): concurrent release/read is unsafe in
        # native backends. A stuck daemon reader is bounded by CaptureLink.close.
        self.thread.join(timeout=0.1)


def _capture_child(pipe, url: str, open_ms: int, read_ms: int):
    # Native FFmpeg/OpenCV errors may contain credentials. Silence native file
    # descriptors in this dedicated process, not the service's logging streams.
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    capture = reader = None
    try:
        capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, open_ms,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, read_ms,
        ])
        if not capture.isOpened():
            pipe.send(("failed", None))
            return
        reader = _LatestCapture(capture)
        pipe.send(("opened", None))
        while pipe.recv() == "read":
            result = reader.read(read_ms / 1000)
            pipe.send(result)
            if result[0] != "frame":
                return
    except (Exception, KeyboardInterrupt):
        try:
            pipe.send(("failed", None))
        except (OSError, EOFError):
            pass
    finally:
        if reader is not None:
            reader.close()
        elif capture is not None:
            capture.release()
        pipe.close()


class CaptureLink:
    """Hard deadline even when a native RTSP backend ignores its timeout."""
    def __init__(self, url: str, config: Config):
        self.config = config
        context = mp.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=_capture_child, args=(child, url,
            int(config.open_timeout_seconds*1000), int(config.read_timeout_seconds*1000)), daemon=True)
        self.process.start()
        child.close()

    def receive(self, timeout: float, stop: threading.Event):
        deadline = time.monotonic()+timeout
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                if self.pipe.poll(min(0.1, max(0, deadline-time.monotonic()))):
                    return self.pipe.recv()
            except (EOFError, OSError):
                return "failed", None
        return "failed", None

    def open(self, stop):
        return self.receive(self.config.open_timeout_seconds+2.0, stop)[0] == "opened"

    def read(self, stop):
        if stop.is_set():
            return "failed", None
        try:
            self.pipe.send("read")
            return self.receive(self.config.read_timeout_seconds+0.5, stop)
        except (EOFError, OSError):
            return "failed", None

    def close(self):
        self.pipe.close()
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=1)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=1)


def rtsp_samples(url: str, config: Config, stop: threading.Event, max_frames=None,
                 link_factory=CaptureLink, clock=time.perf_counter, waiter=None):
    if not url.lower().startswith(("rtsp://", "rtsps://")):
        raise SourceError("RTSP_URL must use rtsp:// or rtsps://")
    waiter = waiter or stop.wait
    delay = config.reconnect_initial_seconds
    count, session = 0, 0
    while not stop.is_set() and (max_frames is None or count < max_frames):
        link = None
        try:
            link = link_factory(url, config)
            if link.open(stop):
                session += 1
                good_frames = 0
                last_sequence = last_t = None
                while not stop.is_set() and (max_frames is None or count < max_frames):
                    kind, image = link.read(stop)
                    if kind != "frame" or stop.is_set():
                        break
                    if isinstance(image, CapturedFrame):
                        frame = image
                        if last_sequence is not None:
                            if frame.sequence <= last_sequence or frame.t <= last_t:
                                break  # do not replay duplicate or regressing frames
                            # Latest-frame sampling deliberately skips decoded frames.
                            # DetectionEngine resets on actual timestamp gaps; a short
                            # sampling skip is not a disconnect or a new session.
                        last_sequence, last_t = frame.sequence, frame.t
                        image, captured_t, occurred_at = frame.image, frame.t, frame.occurred_at
                    else:
                        # Legacy/custom fake links still return a bare ndarray.
                        captured_t, occurred_at = clock(), datetime.now(timezone.utc)
                    count += 1
                    good_frames += 1
                    # A stable connection resets backoff; one-frame flapping does not.
                    if good_frames >= 10:
                        delay = config.reconnect_initial_seconds
                    yield Sample(image, captured_t, occurred_at, session=session)
        except (OSError, EOFError, RuntimeError):
            pass  # credentials and native error strings must never reach logs
        finally:
            if link is not None:
                link.close()
        if stop.is_set() or (max_frames is not None and count >= max_frames):
            break
        import logging
        logging.getLogger(__name__).warning("RTSP disconnected; retry in %.1fs", delay)
        if waiter(delay):
            break
        delay = min(config.reconnect_max_seconds, delay*2)

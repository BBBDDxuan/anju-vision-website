from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
import math
import os
from pathlib import Path
import threading
import uuid

import cv2
import numpy as np

from .config import EvidenceConfig
from .spool import Spool
from .types import Sample, Signal

log = logging.getLogger(__name__)


def atomic_bytes(path: Path, content: bytes):
    temp = path.with_suffix(path.suffix+".partial")
    with temp.open("xb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


@dataclass
class Clip:
    event_id: str
    end: float
    frames: list = field(default_factory=list)


class EvidenceRecorder:
    def __init__(self, spool: Spool, config: EvidenceConfig):
        self.spool, self.config = spool, config
        self.directory = spool.root / "evidence"
        self.directory.mkdir(exist_ok=True)
        self.ring = deque()
        self.active: list[Clip] = []
        self.last_t = -math.inf
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-encoder")
        self.futures = []
        self.encoding_lock = threading.Lock()

    def image(self, image: np.ndarray, simulated: bool):
        height, width = image.shape[:2]
        scale = min(1.0, self.config.max_width/width, self.config.max_height/height)
        # Even dimensions for mp4v. Never modify the caller's frame.
        size = (max(2, int(width*scale)//2*2), max(2, int(height*scale)//2*2))
        result = cv2.resize(image, size)
        if simulated:
            label = "SIMULATION - SYNTHETIC EVIDENCE"
            font = cv2.FONT_HERSHEY_SIMPLEX
            scale = min(0.5, max(0.18, (result.shape[1]-10)/cv2.getTextSize(label, font, 1.0, 1)[0][0]))
            # Source mock labels occupy the top; recorder watermark stays below.
            y = max(10, result.shape[0]-10)
            text_width, text_height = cv2.getTextSize(label, font, scale, 1)[0]
            cv2.rectangle(result, (0, max(0, y-text_height-5)),
                          (min(result.shape[1]-1, text_width+10), result.shape[0]-1), (0, 0, 0), -1)
            cv2.putText(result, label, (5, y), font, scale, (0, 230, 255), 1, cv2.LINE_AA)
        return result

    def push(self, sample: Sample):
        if sample.t < self.last_t:
            self.discontinuity()
        if sample.t-self.last_t + 1e-8 < 1/self.config.fps:
            return
        frame = (sample.t, self.image(sample.image, sample.simulated))
        self.last_t = sample.t
        self.ring.append(frame)
        while self.ring and sample.t-self.ring[0][0] > self.config.pre_seconds+1e-8:
            self.ring.popleft()
        remaining = []
        for clip in self.active:
            clip.frames.append(frame)
            if sample.t + 1e-8 >= clip.end:
                self._submit(clip, "complete")
            else:
                remaining.append(clip)
        self.active = remaining

    def alarm(self, signal: Signal, sample: Sample, device_id: str, model_details: dict):
        event_id = str(uuid.uuid4())
        keyframe = self.directory / f"{event_id}.jpg"
        image = self.image(sample.image, sample.simulated)
        jpg = None
        for quality in (85, 65, 45, 25):
            ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ok and encoded.nbytes <= self.config.max_jpg_bytes:
                jpg = encoded.tobytes()
                break
        if jpg is not None:
            self.spool.check_capacity(len(jpg)+8192)
            atomic_bytes(keyframe, jpg)
        else:
            keyframe = None
        details = dict(signal.details)
        details.update(model_details)
        details.update({"simulated": sample.simulated,
                        "stream_time_seconds": round(sample.t, 4),
                        "clip_association": "append_to_canonical_backend_event_after_upload",
                        "keyframe_available": keyframe is not None})
        payload = {"event_id": event_id, "device_id": device_id,
                   "event_type": signal.event_type, "severity": "critical",
                   "confidence": signal.confidence,
                   "occurred_at": sample.occurred_at.isoformat(),
                   "track_id": signal.track_id, "details": details}
        self.spool.add_event(payload, keyframe)
        # Alarm is now durable and independently deliverable, before post frames.
        self.futures = [f for f in self.futures if not f.done()]
        if len(self.active)+len(self.futures) >= self.config.max_active_clips:
            self.spool.clip_failed(event_id, "clip concurrency budget exceeded")
        else:
            self.active.append(Clip(event_id, sample.t+self.config.post_seconds, list(self.ring)))
        log.info("local event queued: %s simulated=%s", signal.event_type, sample.simulated)
        return event_id

    def _submit(self, clip: Clip, note: str):
        self.futures.append(self.executor.submit(self._encode, clip, note))

    def _encode(self, clip: Clip, note: str):
        target = self.directory / f"{clip.event_id}.mp4"
        temp = self.directory / f"{clip.event_id}.partial.mp4"
        writer = None
        try:
            if len(clip.frames) < 2:
                self.spool.clip_failed(clip.event_id, "insufficient frames")
                return
            size = clip.frames[0][1].shape[1::-1]
            fps = self.config.fps
            count = max(2, int(round((clip.frames[-1][0]-clip.frames[0][0])*fps))+1)
            # Config already caps duration; this also bounds unexpected input gaps.
            count = min(count, math.ceil((self.config.pre_seconds+self.config.post_seconds)*fps)+2)
            self.spool.check_capacity(self.config.max_mp4_bytes)
            writer = cv2.VideoWriter(str(temp), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
            if not writer.isOpened():
                raise RuntimeError("encoder unavailable")
            index = 0
            for i in range(count):
                moment = clip.frames[0][0]+i/fps
                while index+1 < len(clip.frames) and clip.frames[index+1][0] <= moment+1e-8:
                    index += 1
                image = clip.frames[index][1]
                if image.shape[1::-1] != size:
                    image = cv2.resize(image, size)
                writer.write(image)
                if temp.exists() and temp.stat().st_size > self.config.max_mp4_bytes:
                    raise RuntimeError("clip size limit")
            writer.release()
            writer = None
            if not temp.is_file() or not 0 < temp.stat().st_size <= self.config.max_mp4_bytes:
                raise RuntimeError("clip size limit")
            capture = cv2.VideoCapture(str(temp))
            try:
                ok, _ = capture.read()
                if not ok:
                    raise RuntimeError("encoded clip cannot be decoded")
            finally:
                capture.release()
            with temp.open("r+b") as file:
                os.fsync(file.fileno())
            os.replace(temp, target)
            self.spool.attach_clip(clip.event_id, target, note)
        except Exception as exc:
            # The reason category is safe to persist; native exception text isn't.
            self.spool.clip_failed(clip.event_id, "mp4_encode_or_disk_budget_failed")
            log.warning("local clip unavailable (%s); alarm remains queued", type(exc).__name__)
        finally:
            if writer is not None:
                writer.release()
            if temp.exists():
                temp.unlink()

    def discontinuity(self):
        for clip in self.active:
            self._submit(clip, "truncated_by_disconnect_or_eof")
        self.active.clear()
        self.ring.clear()
        self.last_t = -math.inf

    def close(self):
        self.discontinuity()
        self.executor.shutdown(wait=True)
        # Surface ledger/worker exceptions instead of silently dropping failures.
        for future in self.futures:
            future.result()

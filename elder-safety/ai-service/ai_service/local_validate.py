"""Offline real-model inference with annotated video, trace, alarms and evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time

import cv2
import numpy as np

from .config import Config
from .engine import DetectionEngine
from .evidence import EvidenceRecorder
from .models import LocalModels
from .spool import Spool
from .types import Sample


def media_frames(path, image_seconds=3.0):
    path = Path(path)
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Cannot decode image")
        for i in range(round(image_seconds * 10)):
            yield i / 10, 10.0, image.copy()
        return
    if path.suffix.lower() == ".gif":
        from PIL import Image, ImageSequence
        with Image.open(path) as animation:
            t = 0.0
            for frame in ImageSequence.Iterator(animation):
                duration = max(0.01, frame.info.get("duration", 100) / 1000)
                image = cv2.cvtColor(np.asarray(frame.convert("RGB")), cv2.COLOR_RGB2BGR)
                yield t, 1 / duration, image
                t += duration
        return
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError("Cannot decode local video")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or not 0 < fps <= 240:
            raise ValueError("Unsupported video FPS")
        i = 0
        while True:
            ok, image = capture.read()
            if not ok:
                break
            yield i / fps, fps, image
            i += 1
    finally:
        capture.release()


def draw(image, poses, flames, signals, t):
    output = image.copy()
    for pose in poses:
        box = tuple(map(int, pose.bbox))
        cv2.rectangle(output, box[:2], box[2:], (80, 220, 80), 2)
        for x, y, confidence in pose.keypoints:
            if confidence >= 0.55:
                cv2.circle(output, (int(x), int(y)), 3, (255, 200, 20), -1)
    for flame in flames:
        box = tuple(map(int, flame.bbox))
        cv2.rectangle(output, box[:2], box[2:], (0, 100, 255), 2)
        cv2.putText(output, f"{flame.label} {flame.confidence:.2f}", box[:2],
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 100, 255), 1)
    # Put the banner outside the evidence image: a 45px overlay hid raised
    # hands completely in the 160x120 KTH clip. Upscaling is display-only.
    if output.shape[1] < 640:
        scale = 640 / output.shape[1]
        output = cv2.resize(output, (640, round(output.shape[0] * scale)))
    output = cv2.copyMakeBorder(output, 36, 0, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    label = " / ".join(s.event_type for s in signals) or "NO NEW ALARM"
    cv2.putText(output, f"LOCAL YOLO t={t:.2f}s {label}", (5, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)
    return output


def validate_file(source: Path, output: Path, config: Config, models=None, max_frames=None):
    source = source.resolve()
    if not source.is_file():
        raise ValueError("A local input file is required; URLs and camera devices are not opened here")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)  # Never overwrite a previous validation.
    models = models or LocalModels(config.models)
    engine = DetectionEngine(config)
    spool = Spool(output / "spool", "local_validation", "http://127.0.0.1:8010", config.max_spool_bytes)
    recorder = EvidenceRecorder(spool, config.evidence)
    writer = None
    count = 0
    timings, events = [], []
    origin = datetime.now(timezone.utc)
    trace_path = output / "trace.jsonl"
    model_details = {"source_kind": "local_validation", "real_model_validated": True,
                     "validation_scope": "weights loaded and inference executed, not accuracy validation",
                     "pose_license": config.models.pose_license,
                     "flame_license": config.models.flame_license}
    started = time.perf_counter()
    try:
        with trace_path.open("w", encoding="utf-8") as trace:
            for t, fps, image in media_frames(source):
                if max_frames is not None and count >= max_frames:
                    break
                before = time.perf_counter()
                poses, flames = models.infer(image)
                inference_ms = (time.perf_counter() - before) * 1000
                timings.append(inference_ms)
                sample = Sample(image, t, origin + timedelta(seconds=t), poses=poses, flames=flames)
                recorder.push(sample)
                signals = engine.process(sample)
                for signal in signals:
                    recorder.alarm(signal, sample, "local_validation", model_details)
                    row = {"frame": count, "stream_seconds": t, "event_type": signal.event_type,
                           "track_id": signal.track_id, "confidence": signal.confidence, "details": signal.details}
                    events.append(row)
                    print("ALARM " + json.dumps(row, ensure_ascii=False), flush=True)
                trace.write(json.dumps({"frame": count, "t": t, "inference_ms": inference_ms,
                    "poses": [{"bbox": p.bbox, "confidence": p.confidence, "keypoints": p.keypoints.tolist()} for p in poses],
                    "flames": [{"bbox": f.bbox, "confidence": f.confidence, "label": f.label} for f in flames],
                    "events": [s.event_type for s in signals]}, allow_nan=False) + "\n")
                annotated = draw(image, poses, flames, signals, t)
                if writer is None:
                    h, w = annotated.shape[:2]
                    size = (w // 2 * 2, h // 2 * 2)
                    writer = cv2.VideoWriter(str(output / "annotated.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
                    if not writer.isOpened():
                        raise ValueError("Video writer unavailable")
                writer.write(cv2.resize(annotated, size))
                if count == 0 or signals:
                    ok, encoded = cv2.imencode(".jpg", annotated)
                    if not ok:
                        raise ValueError("Cannot encode annotated image")
                    (output / f"frame-{count:05d}.jpg").write_bytes(encoded.tobytes())
                count += 1
    finally:
        recorder.close()
        if writer is not None:
            writer.release()
        spool.close()
    if count == 0:
        raise ValueError("Input has no decoded frames")
    import torch
    report = {"source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "output": str(output), "frames": count, "events": events,
              "device": config.models.device, "torch": torch.__version__, "cuda_available": torch.cuda.is_available(),
              "wall_seconds": time.perf_counter() - started,
              "inference_ms_mean": float(np.mean(timings)),
              "inference_ms_p50_warm": float(np.median(timings[1:] or timings)),
              "inference_ms_p95_warm": float(np.percentile(timings[1:] or timings, 95)),
              "smoke_alarm_enabled": False, "spark_detection_supported": False,
              "help_definition": "continuous conventional two-hands-raised candidate, not audio or intent",
              "evaluation": "small-sample functional validation; not an accuracy estimate"}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args(argv)
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("max-frames must be positive")
    config = Config.load(args.config)
    root = Path(__file__).resolve().parents[2]
    output = args.output or root / "runtime" / ("local-validation-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    report = validate_file(args.input, output, config, max_frames=args.max_frames)
    print(json.dumps({k: report[k] for k in ("output", "frames", "events", "inference_ms_p50_warm")}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

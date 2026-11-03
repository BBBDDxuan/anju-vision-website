"""Bounded real-camera inference; reports statistics only, never images or credentials."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys
import threading
import time
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai-service"))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", required=True)
    result.add_argument("--device", default="0")
    result.add_argument("--seconds", type=float, default=20)
    result.add_argument("--max-frames", type=int, default=180)
    result.add_argument("--expected-host", required=True)
    result.add_argument("--report", type=Path, required=True)
    return result


def summary(values):
    if not values:
        return None
    ordered = sorted(values)
    return {"median": round(statistics.median(values), 3),
            "p95": round(ordered[max(0, math.ceil(len(ordered)*0.95)-1)], 3),
            "max": round(max(values), 3)}


def main(argv=None):
    args = parser().parse_args(argv)
    report = {"status": "failed", "started_at": datetime.now(timezone.utc).isoformat(),
              "real_models": True, "source": "live_rtsp", "media_saved": False,
              "network_uploads": False, "cloud_calls": False, "frames": 0}
    stop = threading.Event()
    link = timer = None
    try:
        if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 60 or not 1 <= args.max_frames <= 600:
            raise ValueError("invalid_bounds")
        load_dotenv(ROOT / ".env", override=False)
        url = os.environ.get("RTSP_URL", "")
        parsed = urlsplit(url)
        if parsed.scheme not in ("rtsp", "rtsps") or parsed.hostname != args.expected_host:
            raise ValueError("unexpected_rtsp_target")
        if not parsed.username or not parsed.password:
            raise ValueError("camera_credentials_missing")
        from ai_service.config import Config
        from ai_service.engine import DetectionEngine
        from ai_service.models import LocalModels
        from ai_service.runtime_env import configure_model_runtime
        from ai_service.sources import CaptureLink, CapturedFrame
        from ai_service.types import Sample
        configure_model_runtime()
        import numpy as np
        import torch
        config = Config.load(args.config)
        config.models.device = args.device
        config.open_timeout_seconds = 4
        config.read_timeout_seconds = 2
        if args.device != "cpu" and not torch.cuda.is_available():
            raise ValueError("cuda_unavailable")
        loaded = time.perf_counter()
        models = LocalModels(config.models)
        def sync():
            if args.device != "cpu":
                torch.cuda.synchronize()
        models.infer(np.zeros((360, 640, 3), np.uint8))
        sync()
        report["model_load_warmup_seconds"] = round(time.perf_counter()-loaded, 3)
        report["pose_device"] = str(next(models.pose.model.parameters()).device) if models.pose else None
        report["fire_device"] = str(next(models.flame.model.parameters()).device) if models.flame else None
        report["selected_fire_classes"] = config.models.flame_classes
        report["help_definition"] = "both_hands_raised_not_semantic_intent"
        report["smoke_detection_enabled"] = "smoke" in config.models.flame_classes
        engine = DetectionEngine(config)
        link = CaptureLink(url, config)
        if not link.open(stop):
            raise RuntimeError("rtsp_open_failed_or_timed_out")
        inference_ms, decode_age_ms, result_age_ms, gaps_ms = [], [], [], []
        counts = Counter()
        events = []
        first_t = last_t = last_sequence = None
        skipped = discontinuities = person_frames = flame_frames = 0
        started = time.monotonic()
        timer = threading.Timer(args.seconds, stop.set)
        timer.daemon = True
        timer.start()
        while not stop.is_set() and report["frames"] < args.max_frames:
            kind, frame = link.read(stop)
            if stop.is_set():
                break
            if kind != "frame" or not isinstance(frame, CapturedFrame):
                raise RuntimeError("capture_interrupted")
            if last_t is not None:
                if frame.t <= last_t or frame.sequence <= last_sequence:
                    raise RuntimeError("capture_timestamp_regression")
                gaps_ms.append((frame.t-last_t)*1000)
                skipped += max(0, frame.sequence-last_sequence-1)
            if first_t is None:
                first_t = frame.t
            decode_age_ms.append(max(0, time.perf_counter()-frame.t)*1000)
            sample = Sample(frame.image, frame.t, frame.occurred_at, session=1)
            sync()
            t0 = time.perf_counter()
            sample.poses, sample.flames = models.infer(frame.image)
            sync()
            inference_ms.append((time.perf_counter()-t0)*1000)
            if engine.discontinuous(sample):
                discontinuities += 1
            signals = engine.process(sample)
            for signal in signals:
                counts[signal.event_type] += 1
                events.append({"event_type": signal.event_type, "offset_seconds": round(frame.t-first_t, 3)})
            person_frames += bool(sample.poses)
            flame_frames += bool(sample.flames)
            result_age_ms.append(max(0, time.perf_counter()-frame.t)*1000)
            report["frames"] += 1
            report["width"], report["height"] = frame.image.shape[1], frame.image.shape[0]
            last_t, last_sequence = frame.t, frame.sequence
        elapsed = time.monotonic()-started
        report.update(status="passed" if report["frames"] >= 10 else "partial",
                      elapsed_seconds=round(elapsed, 3),
                      processed_fps=round(report["frames"]/max(elapsed, 1e-6), 3),
                      capture_span_seconds=round(last_t-first_t, 3) if last_t is not None else 0,
                      person_frames=person_frames, flame_candidate_frames=flame_frames,
                      event_counts=dict(counts), events=events,
                      inference_ms=summary(inference_ms), decode_to_consumer_age_ms=summary(decode_age_ms),
                      decode_to_result_age_ms=summary(result_age_ms), sample_gap_ms=summary(gaps_ms),
                      intentionally_skipped_decoded_frames=skipped, temporal_resets=discontinuities,
                      actual_camera_exposure_latency_measured=False,
                      caveat="Limited live smoke test, not a labelled accuracy or alarm-delivery evaluation")
    except (Exception, KeyboardInterrupt) as exc:
        # Never forward native model/decoder exception strings or source URLs.
        safe_codes = {"invalid_bounds", "unexpected_rtsp_target", "camera_credentials_missing", "cuda_unavailable",
                      "rtsp_open_failed_or_timed_out", "capture_interrupted", "capture_timestamp_regression"}
        report["error"] = str(exc) if str(exc) in safe_codes else type(exc).__name__
    finally:
        stop.set()
        if timer is not None:
            timer.cancel()
        if link is not None:
            link.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

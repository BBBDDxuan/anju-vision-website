from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path
import sys
import threading

from .config import Config
from .engine import DetectionEngine
from .evidence import EvidenceRecorder
from .models import LocalModels, ModelConfigurationError
from .network import BackendClient, DeliveryWorker, backend_url
from .sources import SourceError, SourceHealth, file_samples, mock_samples, rtsp_samples
from .spool import Spool, SpoolFull


def parser():
    result = argparse.ArgumentParser(description="Local elder-safety AI MVP; mock evidence is explicitly synthetic")
    result.add_argument("--source", required=True, help="mock, rtsp, or a local video file path")
    result.add_argument("--backend", required=True, help="backend http(s) origin")
    result.add_argument("--config", help="local JSON configuration")
    result.add_argument("--state-dir", default=str(Path(__file__).resolve().parent.parent / "var"),
                        help="persistent spool/evidence directory (default: ai-service/var)")
    result.add_argument("--max-frames", type=int, help="bound successful frame count; mock defaults to 100")
    result.add_argument("--no-realtime", action="store_true", help="process mock/file as fast as possible")
    result.add_argument("--offline", action="store_true", help="local detection/evidence only, no network")
    result.add_argument("--retry-only", action="store_true", help="retry existing spool without opening source")
    result.add_argument("--drain-seconds", type=float, default=2.0, help="bounded network drain on EOF")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx INFO includes request URLs. Keep network details out of logs.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    log = logging.getLogger(__name__)
    stop = threading.Event()
    spool = recorder = delivery = None
    exit_code = 0
    try:
        config = Config.load(args.config)
        backend = backend_url(args.backend)
        device_id = os.environ.get("SAFETY_DEVICE_ID", "").strip()
        token = os.environ.get("SAFETY_DEVICE_TOKEN", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", device_id):
            raise ValueError("SAFETY_DEVICE_ID must be 1..100 ASCII letters/digits/underscore/hyphen")
        if not token.strip() or "\n" in token or "\r" in token:
            raise ValueError("SAFETY_DEVICE_TOKEN is required and must be single-line")
        if args.max_frames is not None and args.max_frames <= 0:
            raise ValueError("max-frames must be positive")
        if not 0 <= args.drain_seconds <= 30:
            raise ValueError("drain-seconds must be in [0,30]")
        if args.retry_only and args.offline:
            raise ValueError("retry-only requires networking")
        if args.source.lower().startswith(("rtsp://", "rtsps://")):
            raise ValueError("use --source rtsp and RTSP_URL; URLs must not appear in command-line logs")
        rtsp_url = os.environ.get("RTSP_URL", "")
        if args.source == "rtsp" and not args.retry_only and not rtsp_url.lower().startswith(("rtsp://", "rtsps://")):
            raise ValueError("source rtsp requires RTSP_URL with an RTSP scheme")
        # Validate/load local models before starting network delivery.
        models = None if args.source == "mock" or args.retry_only else LocalModels(config.models)
        spool = Spool(Path(args.state_dir), device_id, backend, config.max_spool_bytes)
        health = SourceHealth(min(30.0, max(1.0, config.read_timeout_seconds+0.5)))
        if not args.offline:
            client = BackendClient(backend, device_id, token, config)
            delivery = DeliveryWorker(spool, client, config,
                                      heartbeat_allowed=(lambda: False) if args.retry_only else health.alive)
            delivery.start()
        if args.retry_only:
            log.info("retry-only active; Ctrl+C to stop; queued jobs are durable")
            while not stop.wait(0.25):
                if delivery.thread and not delivery.thread.is_alive():
                    raise RuntimeError("delivery worker stopped")
        else:
            if args.source == "mock":
                log.warning("SIMULATION: deterministic synthetic poses/flames; no real detector is used")
                samples = mock_samples(stop, fps=config.evidence.fps,
                                       max_frames=args.max_frames, realtime=not args.no_realtime)
            elif args.source == "rtsp":
                samples = rtsp_samples(rtsp_url, config, stop, args.max_frames)
            else:
                samples = file_samples(args.source, stop, args.max_frames, not args.no_realtime)
            if models is not None:
                log.info("pose detector: %s; flame detector: %s",
                         "enabled (local weights)" if models.pose else "DISABLED (no weights)",
                         "enabled (local weights)" if models.flame else "DISABLED (no weights)")
            recorder = EvidenceRecorder(spool, config.evidence)
            engine = DetectionEngine(config)
            model_details = {"source_kind": "mock" if args.source == "mock" else "rtsp" if args.source == "rtsp" else "file",
                             "timestamp_basis": "UTC acquisition start plus media time" if args.source != "rtsp" else "UTC acquisition time",
                             "pose_license": config.models.pose_license if models and models.pose else {},
                             "flame_license": config.models.flame_license if models and models.flame else {},
                             "real_model_validated": models is not None,
                             "validation_scope": "inference execution only, not accuracy validation"}
            for sample in samples:
                health.mark_frame()  # Actual acquisition, before possibly slow inference.
                if models is not None:
                    sample.poses, sample.flames = models.infer(sample.image)
                if engine.discontinuous(sample):
                    recorder.discontinuity()
                recorder.push(sample)
                for signal in engine.process(sample):
                    recorder.alarm(signal, sample, device_id, model_details)
                if delivery and delivery.thread and not delivery.thread.is_alive():
                    raise RuntimeError("delivery worker stopped")
            health.clear()
            recorder.close()
            recorder = None
            if delivery:
                import time
                deadline = time.monotonic()+args.drain_seconds
                while spool.pending_count() and time.monotonic() < deadline:
                    stop.wait(0.05)
    except KeyboardInterrupt:
        log.info("stopping; unsent events remain on disk")
    except (ValueError, ModelConfigurationError, SourceError) as exc:
        # Only application-generated configuration categories are printable.
        # JSON errors and model library exceptions may include sensitive input.
        if isinstance(exc, ModelConfigurationError):
            log.error("model configuration rejected: %s", exc)
        else:
            log.error("configuration/source rejected (%s); check README and local settings", type(exc).__name__)
        exit_code = 2
    except SpoolFull:
        log.error("disk budget exhausted; stopped capture; preserve/export spool before retrying")
        exit_code = 3
    except Exception:
        log.error("service failure; durable spool retained; check configuration and local dependencies")
        exit_code = 1
    finally:
        stop.set()
        if "health" in locals():
            health.clear()
        if recorder:
            recorder.close()
        worker_stopped = delivery.stop() if delivery else True
        if spool:
            log.info("pending network jobs: %d; blocked evidence jobs: %d; local event records: %d",
                     spool.pending_count(), spool.blocked_count(), len(spool.events()))
            # An in-flight HTTP operation may finish later; never close its DB.
            if worker_stopped:
                spool.close()
    return exit_code

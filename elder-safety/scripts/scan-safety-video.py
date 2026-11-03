"""Scan a local video for small-model candidates; never upload or notify."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai-service"))


def _emit(status: str, frames: int | None = None, candidates: int | None = None,
          completed: bool = False):
    metadata = {"version": 1, "status": status, "completed": completed}
    if frames is not None:
        metadata.update(frames=frames, candidates=candidates)
    print(json.dumps(metadata, allow_nan=False), flush=True)


SCAN_VERSION = "sampled-local-scan-v3"
DEFAULT_SCAN_FPS = 10.0


def sampling_stride(fps, config, scan_fps=DEFAULT_SCAN_FPS):
    """At least the requested cadence; preserve configured gap/count gates.

    Round the stride down so non-integral rates never undersample the target.
    A count gate must be able to accumulate by its existing hold-time gate.
    Low-FPS source videos stay at source cadence; no frames are synthesized.
    """
    if (isinstance(scan_fps, bool) or not isinstance(scan_fps, (float, int))
            or not math.isfinite(scan_fps) or not 0 < scan_fps <= 240):
        raise ValueError("Invalid scan frequency")
    gap = min(config.fall.max_gap_seconds, config.fire.max_gap_seconds,
              config.tracking.max_gap_seconds, config.help.max_gap_seconds)
    ceilings = [fps / scan_fps, fps * gap]
    for detector in (config.fire, config.help):
        if detector.min_frames > 1:
            ceilings.append(fps * detector.hold_seconds / (detector.min_frames - 1))
    return max(1, math.floor(min(ceilings) + 1e-8))


def scan_video(source: Path, config, output: Path, max_frames: int | None = None,
               scan_fps: float = DEFAULT_SCAN_FPS, resume: bool = False) -> dict:
    from ai_service.runtime_env import configure_model_runtime
    configure_model_runtime()
    import cv2
    from ai_service.models import LocalModels
    from ai_service.safety_candidates import SafetyCandidateCollector, atomic_json, _atomic_bytes
    from ai_service.types import Sample

    if max_frames is not None and (not isinstance(max_frames, int)
                                   or isinstance(max_frames, bool) or max_frames < 1):
        raise ValueError("Invalid frame limit")
    sampling_stride(30.0, config, scan_fps)  # Validate before creating output.
    source = Path(source).resolve()
    # Reject URLs/devices before giving anything to OpenCV: this CLI is offline.
    if not source.is_file():
        raise ValueError("A local video file is required")
    output = Path(output).absolute()
    if output.is_symlink() or (output.exists() and
            bool(getattr(output.stat(), "st_file_attributes", 0) & 0x400)):
        raise ValueError("Invalid scan directory")
    if output.exists() and not resume:
        raise FileExistsError("Scan output already exists")

    def digest_file(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    source_digest = digest_file(source)
    weights = {name: digest_file(value) for name, value in asdict(config.models).items()
               if name.endswith("_weights") and value}
    identity = {"version": SCAN_VERSION, "source_sha256": source_digest,
                "config": asdict(config), "weights_sha256": weights, "requested_fps": scan_fps,
                "detector_sources": {path.name: digest_file(path) for path in
                                     sorted((ROOT / "ai-service/ai_service").glob("*.py"))}}
    namespace = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    marker = output / "resume.json"
    previous = []

    def validate_stored(candidate):
        identifier = candidate["id"]
        directory = output / identifier
        if (not isinstance(identifier, str) or len(identifier) != 35 or
                not identifier.startswith("sc_") or any(c not in "0123456789abcdef" for c in identifier[3:]) or
                directory.is_symlink() or bool(getattr(directory.stat(), "st_file_attributes", 0) & 0x400)):
            raise ValueError("Invalid saved candidate")
        frames = candidate["frames"]
        if not isinstance(frames, list) or not 1 <= len(frames) <= 12:
            raise ValueError("Invalid saved evidence")
        times, identifiers = [], set()
        for frame in frames:
            path = Path(frame["path"])
            if (path.parent != directory or path.is_symlink() or
                    bool(getattr(path.stat(), "st_file_attributes", 0) & 0x400) or
                    not path.is_file() or path.suffix != ".jpg" or not 0 < path.stat().st_size <= 2_000_000 or
                    type(frame["pts_ms"]) is not int or frame["pts_ms"] < 0 or
                    not isinstance(frame["id"], str) or frame["id"] in identifiers or path.stem != frame["id"]):
                raise ValueError("Invalid saved evidence")
            identifiers.add(frame["id"])
            times.append(frame["pts_ms"])
        if times != sorted(set(times)):
            raise ValueError("Invalid saved evidence timeline")
        return candidate

    if output.exists() and marker.is_file():
        if (not marker.is_file() or marker.is_symlink() or
                json.loads(marker.read_text("utf-8")) != identity):
            raise ValueError("Scan resume fingerprint mismatch")
        previous_report = json.loads((output / "scan.json").read_text("utf-8"))
        previous = previous_report["candidates"]
        if not isinstance(previous, list):
            raise ValueError("Invalid saved scan")
        for candidate in previous:
            validate_stored(candidate)
            manifest = output / candidate["id"] / "manifest.json"
            if manifest.is_symlink() or json.loads(manifest.read_text("utf-8")) != candidate:
                raise ValueError("Invalid saved candidate commit")
    else:
        # The backend prepares pipeline.json before spawning so even an early
        # budget/control stop retains a fingerprint. Never reuse other contents.
        if output.exists() and (not resume or {p.name for p in output.iterdir()} - {"pipeline.json"}):
            raise ValueError("Scan resume metadata missing")
        output.mkdir(parents=True, exist_ok=resume)
        atomic_json(marker, identity)

    class VideoCollector(SafetyCandidateCollector):
        """Stable offline identities; keep the collector's detector rules intact."""
        def _save(self, pending):
            candidate = dict(pending.candidate)
            key = [namespace, candidate["event_type"], candidate["track_id"], round(candidate["t"] * fps)]
            candidate["id"] = "sc_" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:32]
            directory = self.output / candidate["id"]
            if directory.is_symlink() or (directory.exists() and
                    bool(getattr(directory.stat(), "st_file_attributes", 0) & 0x400)):
                raise ValueError("Invalid candidate directory")
            directory.mkdir(exist_ok=True)
            manifest = directory / "manifest.json"
            if manifest.is_file():
                if manifest.is_symlink():
                    raise ValueError("Invalid candidate manifest")
                stored = validate_stored(json.loads(manifest.read_text("utf-8")))
                if any(stored.get(k) != candidate[k] for k in ("id", "t", "event_type", "track_id")):
                    raise ValueError("Candidate identity mismatch")
                # Freeze exact previously reviewed frame IDs, bytes and metadata,
                # even if the first scan stopped with a shorter post-roll.
                return stored
            frames = []
            for frame in pending.frames:
                frame_key = [namespace, round(frame.t * fps)]
                identifier = "sf_" + hashlib.sha256(json.dumps(frame_key).encode()).hexdigest()[:32]
                path = directory / (identifier + ".jpg")
                if path.is_symlink():
                    raise ValueError("Invalid evidence path")
                _atomic_bytes(path, frame.jpeg)
                frames.append({"id": identifier, "pts_ms": round(frame.t * 1000), "path": str(path)})
            candidate["frames"] = frames
            atomic_json(manifest, candidate)
            return candidate

    collector = VideoCollector(config, output)
    count, inferred, completed, candidates = 0, 0, False, list(previous)
    published = {c["id"] for c in previous}
    capture = None
    models = None
    fps, expected, stride = None, None, None
    started = time.perf_counter()
    timings = {"model_load_seconds": 0.0, "decode_seconds": 0.0,
               "inference_seconds": 0.0, "collector_seconds": 0.0}
    candidate_timings = []
    phase = "loading"
    scan_fingerprint = None

    def record_ready(ready, t):
        ready = [c for c in ready if c["id"] not in published]
        candidates.extend(ready)
        published.update(c["id"] for c in ready)
        candidate_timings.extend({"id": c["id"], "event_type": c["event_type"],
            "trigger_video_seconds": c["t"], "ready_video_seconds": t,
            "ready_wall_seconds": round(time.perf_counter() - started, 6)} for c in ready)

    def snapshot():
        duration = expected / fps if expected and fps else None
        processed = min(count / fps, duration) if duration else (count / fps if fps else 0.0)
        report = {"version": 1, "frames": count, "completed": completed,
                  "candidates": candidates, "status": phase,
                  "inferred_frames": inferred, "total_frames": expected,
                  "pose_model_passes": getattr(models, "pose_predict_calls", None),
                  "pose_recovery_frames": getattr(models, "pose_recovery_frames", None),
                  "source_fps": fps, "duration_seconds": duration,
                  "processed_video_seconds": processed,
                  "progress": min(100.0, 100.0 * count / expected) if expected else 0.0,
                  "processed_frames": count, "candidate_count": len(candidates),
                  "timestamp_basis": "decoded_frame_index/source_fps; CFR assumption, not decoded source PTS",
                  "source_sha256": source_digest,
                  "scan_policy": {"version": SCAN_VERSION, "requested_fps": scan_fps,
                      "stride": stride, "effective_fps": fps / stride if stride else None},
                  "scan_fingerprint": scan_fingerprint,
                  "timings": {**timings, "wall_seconds": round(time.perf_counter() - started, 6)},
                  "candidate_timings": candidate_timings}
        atomic_json(output / "scan.json", report)
        return report

    snapshot()  # A hard terminate during model load/inference is never complete.
    _emit("progress", count, 0)
    try:
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise ValueError("Cannot open local video")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or not 0 < fps <= 240:
            raise ValueError("Unsupported video FPS")
        raw_expected = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        expected = raw_expected if math.isfinite(raw_expected) and raw_expected > 0 else None
        stride = sampling_stride(fps, config, scan_fps)
        scan_fingerprint = hashlib.sha256(json.dumps({"config": asdict(config),
            "version": SCAN_VERSION, "requested_fps": scan_fps, "stride": stride,
            "source_fps": fps, "resume_namespace": namespace}, sort_keys=True, allow_nan=False).encode()).hexdigest()
        snapshot()
        origin = datetime.now(timezone.utc)
        # Libraries must not pollute the machine-readable progress channel.
        with open(os.devnull, "w", encoding="utf-8") as quiet:
            before = time.perf_counter()
            with redirect_stdout(quiet):
                models = LocalModels(config.models)
            timings["model_load_seconds"] = time.perf_counter() - before
            phase = "scanning"
            snapshot()
            last_snapshot = time.perf_counter()
            while max_frames is None or count < max_frames:
                before = time.perf_counter()
                ok, image = capture.read()
                timings["decode_seconds"] += time.perf_counter() - before
                if not ok:
                    if count == 0 or (expected is not None and expected > count + 0.5):
                        raise ValueError("Video ended before all frames were decoded")
                    # Unknown-length decoder EOF cannot prove a full scan.
                    completed = expected is not None
                    break
                ready = []
                t = count / fps
                if count % stride == 0:
                    before = time.perf_counter()
                    with redirect_stdout(quiet):
                        poses, flames = models.infer(image)
                    timings["inference_seconds"] += time.perf_counter() - before
                    inferred += 1
                    sample = Sample(image, t, origin + timedelta(seconds=t),
                                    poses=poses, flames=flames)
                    before = time.perf_counter()
                    ready = collector.process(sample)
                    timings["collector_seconds"] += time.perf_counter() - before
                    record_ready(ready, t)
                count += 1
                if ready or count % 25 == 0 or time.perf_counter() - last_snapshot >= 1.0:
                    snapshot()
                    _emit("progress", count, len(candidates))
                    last_snapshot = time.perf_counter()
    finally:
        if capture is not None:
            capture.release()
        # On errors/soft cancellation, keep real partial evidence with false
        # completion. A persistence failure propagates, never reports success.
        record_ready(collector.finish(), (count - 1) / fps if fps and count else 0.0)
        phase = "completed" if completed else "partial"
        report = snapshot()
    return report


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default echoes invalid values, which could contain secrets.
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid scan arguments.\n")


def _cancel(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--scan-fps", type=float, default=DEFAULT_SCAN_FPS)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("Invalid frame limit")
    previous = signal.signal(signal.SIGTERM, _cancel)
    try:
        from ai_service.config import Config
        report = scan_video(args.source, Config.load(args.config), args.output,
                            max_frames=args.max_frames, scan_fps=args.scan_fps, resume=args.resume)
        _emit("done", report["frames"], len(report["candidates"]), report["completed"])
        return 0
    except KeyboardInterrupt:
        _emit("cancelled")
        return 130
    except Exception:
        # Do not serialize source paths, exception messages, credentials or env.
        _emit("failed")
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())

"""Read a few frames without models; RTSP credentials stay in the local .env."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai-service"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="rtsp", help="rtsp or a local file; never pass a URL here")
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--deadline", type=float, default=15)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {"status": "failed", "source_kind": "rtsp" if args.source == "rtsp" else "file", "decoded_frames": 0, "real_model_used": False}
    link = capture = None
    try:
        if not 1 <= args.frames <= 300 or not 1 <= args.deadline <= 60:
            raise ValueError("invalid_bounds")
        if args.source.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
            raise ValueError("put_rtsp_url_in_environment")
        load_dotenv(ROOT / ".env", override=False)
        import cv2
        stop = threading.Event()
        start = time.monotonic()
        if args.source == "rtsp":
            from ai_service.config import Config
            from ai_service.sources import CaptureLink, CapturedFrame
            url = os.getenv("RTSP_URL", "")
            if not url.lower().startswith(("rtsp://", "rtsps://")):
                raise ValueError("rtsp_url_not_configured")
            cfg = Config()
            cfg.open_timeout_seconds = min(3, args.deadline / 2)
            cfg.read_timeout_seconds = min(2, args.deadline / 2)
            link = CaptureLink(url, cfg)
            if not link.open(stop):
                raise RuntimeError("rtsp_open_failed_or_timed_out")
        else:
            if not Path(args.source).is_file():
                raise ValueError("video_file_not_found")
            capture = cv2.VideoCapture(args.source)
            if not capture.isOpened():
                raise RuntimeError("file_open_failed")
        while report["decoded_frames"] < args.frames and time.monotonic() - start < args.deadline:
            if link:
                kind, image = link.read(stop)
                ok = kind == "frame"
                if ok and isinstance(image, CapturedFrame):
                    image = image.image
            else:
                ok, image = capture.read()
            if not ok:
                break
            report["decoded_frames"] += 1
            report["width"], report["height"] = image.shape[1], image.shape[0]
        if not report["decoded_frames"]:
            raise RuntimeError("no_decodable_frames")
        report["elapsed_seconds"] = round(time.monotonic() - start, 3)
        report["requested_frames"] = args.frames
        report["status"] = "passed" if report["decoded_frames"] >= args.frames else "partial"
    except (ValueError, RuntimeError) as exc:
        # These are application-defined codes only; do not print native/URL errors.
        report["error"] = str(exc) if str(exc).replace("_", "").isalnum() else type(exc).__name__
    except Exception as exc:
        report["error"] = type(exc).__name__
    finally:
        if link:
            link.close()
        if capture:
            capture.release()
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output)
    return 0 if report["status"] == "passed" else 2

if __name__ == "__main__":
    raise SystemExit(main())

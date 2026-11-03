"""Synthetic codec/timestamp check only. This is NOT an AI recognition test."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evidence/video_probe.json")
    parser.add_argument("--work-dir", default="D:/MemoryOSHomePrep/checks/video")
    args = parser.parse_args()
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    source = work / "synthetic_codec_probe.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (640, 360))
    assert writer.isOpened(), "MP4 writer could not open"
    for i in range(100):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.rectangle(frame, (20 + i * 4, 120), (70 + i * 4, 170), (0, 180, 255), -1)
        cv2.putText(frame, f"SYNTHETIC PROBE frame={i} t={i / 25:.2f}s", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
        writer.write(frame)
    writer.release()
    cap = cv2.VideoCapture(str(source))
    assert cap.isOpened(), "MP4 decode failed"
    fps, count = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    samples = []
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if index % 25 == 0:
            jpg = work / f"frame-{index:03d}.jpg"
            assert cv2.imwrite(str(jpg), frame)
            samples.append({"frame_index": index, "pts_ms_opencv": cap.get(cv2.CAP_PROP_POS_MSEC),
                            "cfr_expected_ms": round(index / fps * 1000),
                            "sha256": hashlib.sha256(jpg.read_bytes()).hexdigest()})
        index += 1
    cap.release()
    assert count == index == 100 and len(samples) == 4
    assert all(abs(s["pts_ms_opencv"] - s["cfr_expected_ms"]) < 1 for s in samples)
    ffmpeg = shutil.which("ffmpeg")
    transcode = {"status": "UNAVAILABLE"}
    if ffmpeg:
        target = work / "synthetic_h264_probe.mp4"
        p = subprocess.run([ffmpeg, "-y", "-i", str(source), "-an", "-vcodec", "libx264",
                            "-pix_fmt", "yuv420p", str(target)], capture_output=True, timeout=30)
        verify = cv2.VideoCapture(str(target))
        decoded, _ = verify.read()
        verify.release()
        transcode = {"return_code": p.returncode, "h264_readable_by_opencv": decoded,
                     "status": "PASS" if p.returncode == 0 and decoded else "FAIL"}
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "opencv": cv2.__version__,
              "input_kind": "SYNTHETIC_CODEC_PROBE_NOT_CAMERA_VIDEO", "source": str(source),
              "fps": fps, "decoded_frames": index, "duration_seconds": index / fps,
              "samples": samples, "ffmpeg_transcode": transcode,
              "limitations": ["No model inference", "No real camera video", "No VFR timestamp validation",
                              "OpenCV decode does not establish browser playback compatibility"]}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

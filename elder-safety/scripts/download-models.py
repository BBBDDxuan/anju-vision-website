"""Download the two explicitly selected first-version weights; cache stays in this project."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = [
    {"file": "yolo11n-pose.pt", "name": "Ultralytics YOLO11n Pose", "license": "AGPL-3.0",
     "source": "https://docs.ultralytics.com/models/yolo11/",
     "url": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt", "bytes": 6255593},
    {"file": "luminous-fire-yolov8n.pt", "name": "luminous0219/fire-and-smoke-detection-yolov8", "license": "AGPL-3.0",
     "source": "https://github.com/luminous0219/fire-and-smoke-detection-yolov8",
     "url": "https://raw.githubusercontent.com/luminous0219/fire-and-smoke-detection-yolov8/c30923f568d915a53fcf85989be9fe5d4e030518/weights/best.pt",
     "bytes": 6262051, "sha256": "ac0a10257b2bc1f20c9d957f8adeeb61dd6140322fc19d0b4a116cb491776d16"},
]

def download(spec, directory):
    target = directory / spec["file"]
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".part")
        request = urllib.request.Request(spec["url"], headers={"User-Agent": "elder-safety-local-validation"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            count = 0
            while block := response.read(1024 * 1024):
                count += len(block)
                if count > spec["bytes"]:
                    raise ValueError("Download exceeds expected file size")
                output.write(block)
        verify(temporary, spec)
        temporary.replace(target)
    digest = verify(target, spec)
    return {**spec, "path": str(target), "downloaded_sha256": digest,
            "integrity": "pinned SHA256" if spec.get("sha256") else "expected size; locally recorded SHA256"}

def verify(path, spec):
    if path.stat().st_size != spec["bytes"]:
        raise ValueError(f"Unexpected size: {path.name}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if spec.get("sha256") and digest != spec["sha256"]:
        raise ValueError(f"SHA256 mismatch: {path.name}")
    return digest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "weights")
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for spec in ARTIFACTS:
        print("Downloading/verifying " + spec["file"], flush=True)
        records.append(download(spec, directory))
    manifest = {"checked_at": datetime.now(timezone.utc).isoformat(), "models": records,
                "usage_review": "Local prototype only. Preserve AGPL notices; review obligations before distribution. No spark class."}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    config = json.loads((ROOT / "ai-service" / "config.example.json").read_text(encoding="utf-8"))
    config["fall"]["min_hip_drop"] = 0.12
    config["fall"]["observation_seconds"] = 2.5
    config["help"]["hold_seconds"] = 0.5  # Development prototype; default reusable rules remain 1 s.
    config["models"].update({"pose_weights": str(directory / ARTIFACTS[0]["file"]),
                             "flame_weights": str(directory / ARTIFACTS[1]["file"]),
                             "flame_classes": ["fire"], "device": "cpu"})
    for kind, spec in zip(("pose", "flame"), ARTIFACTS):
        config["models"][kind + "_license"] = {key: spec[key] for key in ("name", "source", "license")}
        config["models"][kind + "_license"]["usage_review"] = manifest["usage_review"]
    config_path = ROOT / "ai-service" / "config.local-models.json"
    if not config_path.exists():
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Weights and manifest: " + str(directory))
    print("Local config (existing config preserved): " + str(config_path))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

"""Generate a local detector config for the model versions in weights/manifest.json."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pose", required=True, type=Path)
    parser.add_argument("--flame", required=True, type=Path)
    parser.add_argument("--flame-class", required=True, action="append",
                        help="Exact flame class in the selected model; repeat for multiple classes")
    parser.add_argument("--device", default="cpu", help="cpu, or CUDA device such as 0")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "elder-safety/weights/manifest.json").read_text(encoding="utf-8"))
    metadata = dict(zip(("pose", "flame"), manifest["models"]))
    weights = {"pose": args.pose.resolve(), "flame": args.flame.resolve()}
    for kind, path in weights.items():
        if path.suffix.lower() != ".pt" or not path.is_file():
            parser.error(f"{kind}: provide an existing local .pt file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata[kind]["downloaded_sha256"]:
            parser.error(f"{kind}: SHA256 differs from the recorded model version")
    target = ROOT / "elder-safety/ai-service/config.camera-gpu.json"
    if target.exists():
        parser.error("Local config already exists; preserve or rename it before generating another.")
    config = json.loads((ROOT / "elder-safety/ai-service/config.example.json").read_text(encoding="utf-8"))
    for kind, path in weights.items():
        config["models"][kind + "_weights"] = str(path)
        config["models"][kind + "_license"] = {
            key: metadata[kind][key] for key in ("name", "source", "license")
        }
        config["models"][kind + "_license"]["usage_review"] = (
            "Local prototype; retain upstream notices and review upstream license obligations."
        )
    config["models"]["flame_classes"] = args.flame_class
    config["models"]["device"] = args.device
    # Recording stays disabled by Config defaults; no private RTSP URL enters JSON.
    with target.open("x", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print("Created local detector config. Review it before enabling camera or analysis.")


if __name__ == "__main__":
    main()

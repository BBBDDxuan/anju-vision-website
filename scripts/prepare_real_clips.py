"""Create bounded continuous clips and preserve exact source mappings; never overwrite originals."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import ROOT, Settings
from backend.media import file_sha256, inspect_video


def main():
    settings = Settings.from_env()
    audit = ROOT / "data/validation/video-audit"
    inventory = json.loads((audit / "inventory.json").read_text(encoding="utf-8"))
    out = ROOT / "data/validation/clips"
    out.mkdir(parents=True, exist_ok=True)
    # Selected after reviewing full-length 10-second contact sheets, not from filename assumptions.
    plans = [(1, 16, 36, "cabinet-notebook-016-052.mp4"), (3, 140, 24, "desk-transfer-140-164.mp4")]
    results = []
    for index, start, length, name in plans:
        record = inventory[index]
        assert record["orientation_visually_verified"]
        source, target = ROOT / record["file"], out / name
        if not target.exists() or target.stat().st_size == 0:
            assert target.resolve().parent == out.resolve() and source.resolve() != target.resolve()
            command = [settings.ffmpeg, "-v", "error", "-y", "-i", str(source), "-ss", str(start), "-t", str(length), "-an", "-vf",
                       "scale=960:trunc(960/iw*ih/2)*2,setpts=PTS-STARTPTS", "-vcodec", "libx264", "-bf", "0", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
            result = subprocess.run(command, capture_output=True, timeout=180)
            if result.returncode:
                raise RuntimeError("Clip transcode failed; original videos remain unchanged")
        meta = inspect_video(target, settings)
        results.append({"clip": str(target.relative_to(ROOT)), "clip_sha256": file_sha256(target),
                        "source": record["file"], "source_sha256": record["sha256"], "source_start_ms": start * 1000,
                        "source_end_ms": (start + length) * 1000, "mapping": "source_time_ms = clip_time_ms + source_start_ms",
                        "additional_rotation": "none", "metadata": meta})
    (out / "clip-mapping.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

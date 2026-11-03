"""Read-only original-video inventory and contact sheets. Never rewrites originals."""
import hashlib
import json
from pathlib import Path
import subprocess

import cv2
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "data" / "validation" / "video-audit"
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 17)
    sources = sorted(set((ROOT / "video").glob("*.mp4")) | set(ROOT.glob("*.mp4")))
    records = []
    for index, path in enumerate(sources):
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-print_format", "json", str(path)], capture_output=True, timeout=30)
        if probe.returncode:
            records.append({"file": str(path.relative_to(ROOT)), "probe": "FAIL"})
            continue
        info = json.loads(probe.stdout)
        stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        duration = float(stream.get("duration") or info["format"]["duration"])
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        cap = cv2.VideoCapture(str(path))
        opened = cap.isOpened()
        canvas = Image.new("RGB", (1080, 370), "#f4f3ec")
        draw = ImageDraw.Draw(canvas)
        draw.text((12, 8), f"{path.relative_to(ROOT)} · {duration:.2f}s", font=font, fill="#183c36")
        previews = []
        for col, fraction in enumerate([.1, .5, .9]):
            ms = round(duration * fraction * 1000)
            cap.set(cv2.CAP_PROP_POS_MSEC, ms)
            ok, image = cap.read()
            if ok:
                rgb = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                rgb.thumbnail((350, 300))
                canvas.paste(rgb, (col * 360 + (360 - rgb.width) // 2, 40))
                draw.text((col * 360 + 10, 344), f"{ms/1000:.1f}s", font=font, fill="#183c36")
                previews.append({"requested_ms": ms, "decoded_ms": cap.get(cv2.CAP_PROP_POS_MSEC), "width": image.shape[1], "height": image.shape[0]})
        orient = cap.get(cv2.CAP_PROP_ORIENTATION_META)
        auto = cap.get(cv2.CAP_PROP_ORIENTATION_AUTO)
        cap.release()
        contact = out / f"contact-{index}.jpg"
        canvas.save(contact, quality=88)
        record = {"file": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": h.hexdigest(), "duration_seconds": duration,
                  "width": stream.get("width"), "height": stream.get("height"), "codec": stream.get("codec_name"), "fps": stream.get("avg_frame_rate"),
                  "rotate_tag": stream.get("tags", {}).get("rotate", "0"), "side_data": stream.get("side_data_list", []),
                  "opencv_open": opened, "opencv_rotation_meta": orient, "opencv_rotation_auto": auto, "previews": previews,
                  "contact_sheet": str(contact), "orientation_visually_verified": False}
        records.append(record)
    (out / "inventory.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

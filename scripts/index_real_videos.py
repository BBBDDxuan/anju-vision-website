"""Index the visually verified, correctly oriented user videos without modifying them."""
import json
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / "data/validation/video-audit"
    records = json.loads((folder / "inventory.json").read_text(encoding="utf-8"))
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    for index in [1, 3]:
        record = records[index]
        record["orientation_visually_verified"] = True
        record["orientation_review"] = "Contact sheet manually inspected: people and shelving upright; no additional rotation applied."
        source = ROOT / record["file"]
        cap = cv2.VideoCapture(str(source))
        times = list(range(0, int(record["duration_seconds"]), 10))
        canvas = Image.new("RGB", (1800, ((len(times) + 4) // 5) * 215), "#f4f3ec")
        draw = ImageDraw.Draw(canvas)
        for n, second in enumerate(times):
            cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            image.thumbnail((352, 192))
            x, y = n % 5 * 360, n // 5 * 215
            canvas.paste(image, (x, y))
            draw.text((x + 5, y + 192), f"原片 {second:03d}s", fill="#183c36", font=font)
            if index == 1 and second == 10:
                image.save(folder / "probe-frame.jpg", quality=90)
        cap.release()
        canvas.save(folder / f"dense-{index}.jpg", quality=90)
        record["segments"] = [{"start_ms": sec * 1000, "end_ms": min((sec + 60) * 1000, round(record["duration_seconds"] * 1000))} for sec in range(0, int(record["duration_seconds"]), 60)]
    (folder / "inventory.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Created dense contact sheets and full-length segment indexes for two visually verified sources.")


if __name__ == "__main__":
    main()

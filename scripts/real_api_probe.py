"""One bounded real request per stage; no secrets in reports."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import ROOT, Settings
from backend.db import Database
from backend.errors import AppError
from backend.providers.qwen import QwenProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["text", "image"])
    parser.add_argument("--image")
    args = parser.parse_args()
    settings = Settings.from_env()
    report_path = ROOT / "data/validation" / f"real-probe-{args.stage}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists() and json.loads(report_path.read_text(encoding="utf-8")).get("status") == "PASS":
        print("Already passed; skipped to avoid duplicate paid call.")
        return
    content = [{"type": "text", "text": "请只回复：连接成功" if args.stage == "text" else "简短描述这张真实视频帧里直接可见的物体和场景。不要猜测看不清的细节。"}]
    if args.stage == "image":
        if not args.image:
            raise SystemExit("--image is required")
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(Path(args.image).read_bytes()).decode()}, "max_pixels": 960 * 960})
    db = Database(settings.data_dir)
    record = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "stage": args.stage, "model": settings.model,
              "endpoint": settings.base_url, "is_mock": False, "billing_mode": settings.billing_mode, "rmb_cost": None}
    try:
        text, meta = QwenProvider(settings, db).request(content=content, video_id="validation-probe", job_id="validation-probe-20261018",
                                                     output_tokens=96 if args.stage == "text" else 384, input_bound=8192, kind=args.stage)
        record.update(status="PASS", response=text.replace(settings.api_key, "[REDACTED]"), metadata=meta)
    except AppError as exc:
        record.update(status="BLOCKED", error=exc.public())
    report_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

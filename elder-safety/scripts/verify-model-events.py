"""Replay actual local-model alarms/evidence into an isolated in-process backend. No cloud/push."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation", type=Path, required=True)
    args = parser.parse_args()
    validation = args.validation.resolve()
    summary = json.loads((validation / "summary.json").read_text(encoding="utf-8"))
    from fastapi.testclient import TestClient
    from safety_backend.config import Settings
    from safety_backend.main import create_app
    user_token, device_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    data_dir = ROOT / "runtime" / ("model-event-backend-" + uuid.uuid4().hex)
    settings = Settings(data_dir=data_dir, user_token=user_token,
                        device_tokens={"local_validation": device_token}, worker_enabled=False,
                        review_mode="mock", push_mode="mock")
    app = create_app(settings)
    device = {"Authorization": "Bearer " + device_token}
    user = {"Authorization": "Bearer " + user_token}
    canonical = {}
    evidence_count = 0
    submissions = 0
    with TestClient(app) as client:
        assert client.post("/api/v1/devices/register", headers=device,
                           json={"device_id": "local_validation", "name": "Local model verification"}).status_code == 200
        for item in summary:
            if not item.get("events"):
                continue
            spool_path = Path(item["output"]) / "spool" / "spool.sqlite3"
            with sqlite3.connect(spool_path) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute("SELECT * FROM events ORDER BY rowid").fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                payload["occurred_at"] = datetime.now(timezone.utc).isoformat()
                for field, column, mime in (("evidence_id", "keyframe", "image/jpeg"),
                                            ("clip_evidence_id", "clip_path", "video/mp4")):
                    path = Path(row[column]) if row[column] else None
                    if path:
                        response = client.post("/api/v1/evidence", headers=device,
                            data={"device_id": "local_validation"}, files={"file": (path.name, path.read_bytes(), mime)})
                        assert response.status_code == 200, response.text
                        payload[field] = response.json()["evidence_id"]
                        evidence_count += 1
                response = client.post("/api/v1/events", headers=device, json=payload)
                assert response.status_code == 200, response.text
                result = response.json()
                submissions += 1
                canonical[result["event_id"]] = result["event_type"]
        assert set(canonical.values()) == {"suspected_fall", "fire_detected", "help_requested"}
        while app.state.worker.step(("notify", "review")):
            pass
        for event_id in canonical:
            result = client.get("/api/v1/events/" + event_id, headers=user).json()
            assert result["notification_status"] == "sent"
            assert result["review_status"] == "completed"
            for field in ("evidence_id", "clip_evidence_id"):
                assert result[field]
                response = client.get("/api/v1/evidence/" + result[field], headers=user)
                assert response.status_code == 200 and response.content
            assert client.post(f"/api/v1/events/{event_id}/ack", headers=user).json()["state"] == "acknowledged"
            assert client.post(f"/api/v1/events/{event_id}/resolve", headers=user).json()["state"] == "resolved"
    report = {"passed": True, "source": str(validation), "submissions": submissions,
              "canonical_events": list(canonical.values()), "uploaded_evidence": evidence_count,
              "notification": "mock provider only; no phone delivery", "review": "mock only; no paid API",
              "transport": "FastAPI TestClient in process; separate-process synthetic smoke is tested separately",
              "backend_data": str(data_dir), "credentials": "random per-run tokens not persisted in report"}
    (validation / "backend-replay.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

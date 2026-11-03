"""Real local HTTP + separate AI mock process, no paid APIs or camera required."""
from __future__ import annotations
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]

def wait_for(check, timeout, label):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError("Timed out: " + label)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="also save a sanitized report to this path")
    args = parser.parse_args()
    run = ROOT / "runtime" / ("smoke_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3))
    run.mkdir(parents=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    user, device = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update({"PYTHONPATH": os.pathsep.join([str(ROOT / "backend"), str(ROOT / "ai-service")]),
        "SAFETY_USER_TOKEN": user, "SAFETY_DEVICE_TOKENS": json.dumps({"camera_001": device}),
        "SAFETY_DEVICE_ID": "camera_001", "SAFETY_DEVICE_TOKEN": device,
        "SAFETY_DATABASE_URL": "sqlite:///" + (run / "safety.db").as_posix(), "SAFETY_DATA_DIR": str(run),
        "SAFETY_REVIEW_MODE": "mock", "SAFETY_PUSH_MODE": "mock", "SAFETY_ALLOW_CLOUD_IMAGES": "false",
        "SAFETY_OFFLINE_SECONDS": "60", "SAFETY_RETRY_SECONDS": "0.2", "SAFETY_REVIEW_INTERVAL_SECONDS": "0",
        "PYTHONUTF8": "1"})
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "failed", "mode": "synthetic_AI_mock_review_mock_push", "run_dir": str(run), "real_camera_tested": False, "real_models_tested": False, "real_cloud_tested": False, "phone_app_tested": False}
    server = ai = None
    try:
        with (run / "backend.log").open("w", encoding="utf-8") as server_log, httpx.Client(base_url=base, headers={"Authorization": "Bearer " + user}, timeout=5, trust_env=False) as client:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "safety_backend.main:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def healthy():
                if server.poll() is not None:
                    raise RuntimeError("Backend exited; see local backend.log")
                try:
                    return client.get("/health").status_code == 200
                except httpx.HTTPError:
                    return False
            wait_for(healthy, 20, "backend health")
            assert client.get("/api/v1/events", headers={"Authorization": "Bearer invalid"}).status_code == 401
            with (run / "ai.log").open("w", encoding="utf-8") as ai_log:
                ai = subprocess.Popen([sys.executable, "-m", "ai_service", "--source", "mock", "--backend", base,
                    "--state-dir", str(run / "ai"), "--no-realtime", "--max-frames", "100", "--drain-seconds", "15"], cwd=ROOT, env=env, stdout=ai_log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                code = ai.wait(timeout=50)
                if code != 0:
                    raise RuntimeError("AI process failed; see local ai.log")
            def completed():
                response = client.get("/api/v1/events")
                response.raise_for_status()
                events = response.json()
                kinds = {e["event_type"] for e in events}
                if {"suspected_fall", "fire_detected"}.issubset(kinds) and all(e["notification_status"] == "sent" and e["review_status"] == "completed" for e in events):
                    return events
                return None
            events = wait_for(completed, 15, "two simulated hazards with mock notifications and reviews")
            report["devices"] = client.get("/api/v1/devices").json()
            assert report["devices"] and report["devices"][0]["online"]
            verified = []
            for item in events:
                assert item.get("details", {}).get("simulated") is True, "Simulation must be labeled"
                event_id = item["event_id"]
                assert item.get("evidence_id"), "Keyframe must be linked"
                response = client.get("/api/v1/evidence/" + item["evidence_id"])
                assert response.status_code == 200 and response.headers["content-type"].startswith("image/")
                (run / (item["event_type"] + ".jpg")).write_bytes(response.content)
                assert client.get("/api/v1/evidence/" + item["evidence_id"], headers={"Authorization": "Bearer invalid"}).status_code == 401
                assert item.get("clip_evidence_id"), "Short video must be attached after alert"
                clip = client.get("/api/v1/evidence/" + item["clip_evidence_id"])
                assert clip.status_code == 200 and clip.content[4:8] == b"ftyp"
                ack = client.post(f"/api/v1/events/{event_id}/ack")
                assert ack.status_code == 200 and ack.json()["state"] == "acknowledged"
                resolved = client.post(f"/api/v1/events/{event_id}/resolve")
                assert resolved.status_code == 200 and resolved.json()["state"] == "resolved"
                verified.append({"event_id": event_id, "event_type": item["event_type"], "keyframe": True, "clip": True,
                    "mock_notification": "sent", "mock_review": "completed", "acknowledged": True, "resolved": True})
            import sqlite3
            queue_path = run / "ai" / "spool.sqlite3"
            assert queue_path.is_file(), "AI durable queue must exist"
            with sqlite3.connect(str(queue_path)) as queue:
                pending = queue.execute("SELECT COUNT(*) FROM jobs WHERE done=0").fetchone()[0]
                blocked = queue.execute("SELECT COUNT(*) FROM jobs WHERE done=1 AND last_error IS NOT NULL").fetchone()[0]
            assert pending == 0 and blocked == 0, "AI queue must drain without association errors"
            report["ai_queue"] = {"pending": pending, "blocked": blocked}
            report["events"] = verified
            report["status"] = "passed"
            schema = client.get("/openapi.json")
            schema.raise_for_status()
            (run / "openapi.json").write_text(json.dumps(schema.json(), ensure_ascii=False, indent=2), encoding="utf-8")
            for item in events:
                assert client.delete("/api/v1/events/" + item["event_id"]).status_code == 200
            assert client.get("/api/v1/events").json() == []
            assert not list((run / "evidence").iterdir()), "Backend evidence must be removed"
            report["event_and_evidence_delete"] = True
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, (AssertionError, RuntimeError)):
            report["reason"] = str(exc)
        # Only assertions/application-generated messages appear here, no request credentials.
        print("Smoke failed:", type(exc).__name__)
    finally:
        for process in (ai, server):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        output = json.dumps(report, ensure_ascii=False, indent=2)
        (run / "report.json").write_text(output, encoding="utf-8")
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output, encoding="utf-8")
        print(output)
    return 0 if report["status"] == "passed" else 1

if __name__ == "__main__":
    raise SystemExit(main())

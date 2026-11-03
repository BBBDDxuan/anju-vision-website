"""Limited real-model integration run. Resumes existing uploads/jobs, never fabricates events."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/validation/real-pipeline.json"


def main():
    report = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"is_mock": False, "clips": []}
    def save():
        report["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=90) as client:
        health = client.get("/api/health").json()
        assert health["analysis_ready"] and not health["is_mock"], health
        report["model"] = health["model"]
        report["billing_mode"] = health["billing_mode"]
        for filename in ["cabinet-notebook-016-052.mp4", "desk-transfer-140-164.mp4"]:
            record = next((r for r in report["clips"] if r["filename"] == filename), None)
            if not record:
                with (ROOT / "data/validation/clips" / filename).open("rb") as stream:
                    response = client.post("/api/videos", files={"file": (filename, stream, "video/mp4")})
                response.raise_for_status()
                record = {"filename": filename, "video_id": response.json()["video_id"], "upload": "PASS"}
                report["clips"].append(record)
                save()
            vid = record["video_id"]
            if record.get("job", {}).get("status") == "failed" and record["job"].get("calls") == 0:
                record.setdefault("previous_failures", []).append(record.pop("job"))
                record.pop("job_id", None)
            if not record.get("job_id"):
                response = client.post(f"/api/videos/{vid}/analyze")
                response.raise_for_status()
                record["job_id"] = response.json()["id"]
                save()
            while True:
                job = client.get(f"/api/jobs/{record['job_id']}").json()
                print(filename, job["status"], job["stage"], flush=True)
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(8)
            record["job"] = job
            if job["status"] in {"completed", "partial"}:
                memory = client.get(f"/api/videos/{vid}/events").json()
                (OUT.parent / (filename.removesuffix(".mp4") + "-memory.json")).write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
                record.update(events=len(memory["events"]), entities=len(memory["entities"]), schema="PASS")
                counts = {e["id"]: sum(event["subject_id"] == e["id"] for event in memory["events"]) for e in memory["entities"]}
                candidates = sorted(memory["entities"], key=lambda e: counts[e["id"]], reverse=True)[:3]
                record["queries"] = []
                evidence_ids = {e["id"] for e in memory["evidence"]}
                for entity in candidates:
                    for question in [f"{entity['label']}最后在哪里出现？", f"{entity['label']}的移动经过是什么？", f"能确定{entity['label']}现在在哪里吗？"]:
                        answer = client.post(f"/api/videos/{vid}/query", json={"question": question, "entity_id": entity["id"]}).json()
                        assert all(ref in evidence_ids for claim in answer["observations"] + answer["inferences"] for ref in claim["evidence_ids"])
                        if "现在" in question:
                            assert answer["unknowns"]
                        record["queries"].append(answer)
                    replay = client.get(f"/api/videos/{vid}/replay", params={"entity_id": entity["id"]})
                    assert replay.status_code == 200 and not replay.json()["is_mock"]
                unknown = client.post(f"/api/videos/{vid}/query", json={"question": "不存在的紫色护照在哪里？"}).json()
                assert unknown["status"] == "unknown" and not unknown["evidence"]
                record["unknown_object"] = "PASS"
                record["automated_result"] = "PASS" if job["status"] == "completed" else "PARTIAL"
            else:
                record["automated_result"] = "FAIL"
            save()
        report["usage"] = client.get("/api/health").json()["usage"]
        save()
    print(json.dumps({"clips": [{k: r.get(k) for k in ["filename", "events", "entities", "automated_result"]} for r in report["clips"]], "usage": report["usage"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

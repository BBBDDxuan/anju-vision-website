"""Bounded, real-model A/B test. Uses whole-clip sampled frames, not production windows.

Preparation is offline. --run sends at most four paid requests, with no retries.
Raw responses stay under the ignored data directory; no credentials are exported.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.answer import query_memory
from backend.config import Settings
from backend.db import Database
from backend.errors import AppError
from backend.events import normalize, validate_memory
from backend.jobs import JobManager
from backend.media import extract_frames, file_sha256, inspect_video
from backend.providers.qwen import QwenProvider

MODELS = ("qwen3.6-plus", "qwen3.8-max")
SOURCES = ("10月18日_逆时针90度.mp4", "桌面_逆时针90度.mp4")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(settings, directory):
    directory.mkdir(parents=True, exist_ok=False)
    clips = []
    for index, name in enumerate(SOURCES):
        source = ROOT / "video" / name
        metadata = inspect_video(source, settings)
        video = {"id": "ab_" + uuid4().hex, "name": name,
                 "original_path": str(source), "playback_path": str(source),
                 "sha256": file_sha256(source), "duration_ms": metadata["duration_ms"],
                 "metadata": metadata}
        frames = extract_frames(video, directory / f"clip-{index}" / "frames", settings)
        if len(frames) > 220:
            raise ValueError("This bounded test supports at most 220 sampled frames per clip.")
        clips.append({"video": video, "frames": frames})
        print(json.dumps({"prepared": name, "duration_ms": video["duration_ms"],
                          "sampled_frames": len(frames)}, ensure_ascii=False), flush=True)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "is_mock": False,
                "mode": "whole_clip_sampled_images_ab_not_production_windowed_analysis",
                "sampling_fps": settings.sampling_fps, "models": list(MODELS),
                "maximum_new_requests": 4, "max_output_tokens_per_request": 16384,
                "clips": clips}
    write_json(directory / "manifest.json", manifest)


def run(settings, directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    report_path = directory / "report.json"
    if report_path.exists():
        raise RuntimeError("A run already exists. Inspect saved responses; do not repeat paid calls.")
    if manifest["models"] != list(MODELS) or len(manifest["clips"]) != 2:
        raise ValueError("Unexpected test manifest; refusing paid calls.")
    if any(file_sha256(Path(c["video"]["original_path"])) != c["video"]["sha256"]
           for c in manifest["clips"]):
        raise ValueError("Source video changed since preparation.")
    db = Database(settings.data_dir)
    usage_start = db.usage_summary()
    if usage_start["requests"] + 4 > settings.max_daily_calls:
        raise RuntimeError("The existing daily request budget cannot cover this test.")
    report = {k: v for k, v in manifest.items() if k != "clips"}
    report.update(starting_usage=usage_start, cases=[], actual_rmb_cost=None,
                  limitation="Same 0.5fps frames and prompt; whole-clip key-event summaries, not exhaustive production-window timelines. No held-out accuracy score.")
    write_json(report_path, report)
    # Start with the new model; a model-list entry alone does not prove vision works.
    for model in reversed(MODELS):
        provider = QwenProvider(replace(settings, model=model), db)
        manager = JobManager(settings, db, provider)
        try:
            for index, clip in enumerate(manifest["clips"]):
                video, frames = clip["video"], clip["frames"]
                window = {"start_ms": 0, "end_ms": video["duration_ms"], "frames": frames}
                job_id = "ab-" + directory.name + "-" + model + "-" + str(index)
                case = {"model": model, "source": video["name"], "video_id": video["id"],
                        "video_sha256": video["sha256"], "job_id": job_id,
                        "frame_ids": [f["id"] for f in frames], "frame_count": len(frames),
                        "prompt_version": provider.event_prompt.version,
                        "prompt_sha256": provider.event_prompt.sha256, "status": "running"}
                report["cases"].append(case)
                write_json(report_path, report)
                print("REAL_REQUEST", model, index, len(frames), flush=True)
                started = time.monotonic()
                try:
                    result = provider.extract(window=window, entities=[], video=video,
                                              job_id=job_id, output_tokens=16384)
                    case["usage"] = result.usage
                    case["extraction"] = result.extraction.model_dump()
                    entities, evidence, events = normalize(result.extraction, video["id"], window, {})
                    document = manager.document(video, frames, entities, evidence, events)
                    validate_memory(document, {f["id"]: f for f in frames})
                    memory_path = directory / f"memory-{model}-clip-{index}.json"
                    write_json(memory_path, document)
                    labels = {e["id"]: e["label"] for e in document["entities"]}
                    case["events_summary"] = [{"subject": labels[e["subject_id"]],
                                               "action": e["action"], "target": labels.get(e["target_id"]),
                                               "start_ms": e["start_ms"], "end_ms": e["end_ms"],
                                               "frame_ids": next((ev["frame_ids"] for ev in evidence if ev["id"] in e["evidence_ids"]), []),
                                               "uncertainty_reasons": e["uncertainty_reasons"]}
                                              for e in document["events"]]
                    job = {"id": job_id, "status": "completed", "is_mock": False, "failed_windows": []}
                    answers = []
                    for entity in document["entities"]:
                        if not any(e["subject_id"] == entity["id"] for e in document["events"]):
                            continue
                        question = entity["label"] + "最后在哪里出现？"
                        answer = query_memory(document, job, question, entity_id=entity["id"])
                        valid = {ev["id"] for ev in evidence}
                        if not all(ref in valid for claim in answer["observations"] + answer["inferences"]
                                   for ref in claim["evidence_ids"]):
                            raise ValueError("Answer references evidence outside the real extraction.")
                        answers.append({"question": question, "answer": answer})
                    case.update(status="schema_and_evidence_references_pass", events=len(events),
                                entities=len(entities), memory_file=memory_path.name, answers=answers)
                except AppError as exc:
                    case.update(status="failed", error=exc.public())
                except (ValueError, TypeError, KeyError) as exc:
                    case.update(status="failed", error={"code": type(exc).__name__,
                                "message": str(exc).replace(settings.api_key, "[REDACTED]")})
                case["elapsed_seconds"] = round(time.monotonic() - started, 2)
                report["ending_usage"] = db.usage_summary()
                report["new_requests"] = report["ending_usage"]["requests"] - usage_start["requests"]
                write_json(report_path, report)
                print(json.dumps({k: case[k] for k in ["model", "source", "status", "elapsed_seconds"]}, ensure_ascii=False), flush=True)
                if case["status"] == "failed":
                    # Invalid vision/output is retained honestly; no automatic paid repair.
                    print("STOPPED_AFTER_FAILURE; raw response retained when supplied by provider", flush=True)
                    return
        finally:
            manager.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["sources_unchanged"] = all(file_sha256(Path(c["video"]["original_path"])) == c["video"]["sha256"]
                                       for c in manifest["clips"])
    write_json(report_path, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Send at most four real paid requests; never retry automatically.")
    args = parser.parse_args()
    settings = replace(Settings.from_env(), sampling_fps=.5, timeout=180)
    directory = args.directory.resolve()
    if not directory.is_relative_to((settings.data_dir / "model-comparison").resolve()):
        raise ValueError("Experiment files must remain under DATA_DIR/model-comparison.")
    if args.run:
        run(settings, directory)
    else:
        prepare(settings, directory)


if __name__ == "__main__":
    main()

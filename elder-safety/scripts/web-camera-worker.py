"""Owned GPU subprocess: RTSP -> local models -> annotated preview with Qwen-reviewed durable alarms."""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import json
import os
import queue
from uuid import uuid4
from pathlib import Path
import struct
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai-service"))



def apply_review_result(candidate, entry, result, alerts):
    """Only a successful, matching cloud review may enter the phone alarm outbox."""
    verdict = result.get("verdict")
    if (result.get("candidate_id") != candidate["id"] or
            verdict not in {"confirmed", "rejected", "uncertain", "unavailable"} or
            (verdict == "confirmed" and (result.get("cloud_images") is not True or not result.get("model")))):
        verdict = "unavailable"
    entry.update(review_status=verdict, review_reason=str(result.get("reason", ""))[:800],
                 review_model=result.get("model"))
    if verdict != "confirmed" or alerts is None:
        return
    from ai_service.types import Signal
    reviewed_signal = Signal(candidate["event_type"], candidate["track_id"], candidate["confidence"],
        {"review_status": "confirmed", "review_model": result["model"], "candidate_id": candidate["id"],
         "cloud_images": True, "review_reason": entry["review_reason"]})
    entry["event_id"] = alerts.alarm(reviewed_signal, datetime.fromisoformat(candidate["occurred_at"]))


def main():
    # Keep a private binary output descriptor. Silence library/native diagnostics,
    # which may include a source URL. Only our framed protocol leaves the worker.
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    stop = threading.Event()
    last_ping = [time.monotonic()]
    review_results = queue.Queue(maxsize=32)
    def commands():
        for line in sys.stdin.buffer:
            if line.strip() == b"stop":
                break
            if line.strip() == b"ping":
                last_ping[0] = time.monotonic()
            elif line.lstrip().startswith(b"{") and len(line) <= 8192:
                try:
                    review_results.put_nowait(json.loads(line))
                except (ValueError, queue.Full):
                    pass
        stop.set()
    threading.Thread(target=commands, daemon=True).start()
    def watchdog():
        while not stop.wait(1):
            if time.monotonic()-last_ping[0] > 30:
                stop.set()
    threading.Thread(target=watchdog, daemon=True).start()
    def send(info, jpeg=b""):
        if recorder is not None:
            recording_status = recorder.status()
            info["recording_status"] = recording_status
            info["recording"] = recording_status["state"] == "recording"
        data = json.dumps(dict(info, jpeg_size=len(jpeg)), ensure_ascii=False, separators=(",", ":")).encode()
        output.write(struct.pack("!I", len(data))+data+jpeg)
    info = {"state": "starting", "frames": 0, "events": [], "recording": False, "cloud_calls": False,
            "smoke_enabled": False, "sparks_enabled": False,
            "safety_pipeline": "small_models_then_qwen"}
    link = None
    alerts = None
    fire_worker = None
    recorder = None
    try:
        info["stage"] = "config"
        send(info)
        from dotenv import dotenv_values
        from urllib.parse import urlsplit
        values = dotenv_values(ROOT / ".env")
        url = values.get("RTSP_URL") or ""
        parsed = urlsplit(url)
        if parsed.scheme not in ("rtsp", "rtsps") or not parsed.hostname or not parsed.username or not parsed.password:
            raise ValueError
        from ai_service.runtime_env import configure_model_runtime
        configure_model_runtime()
        info["stage"] = "import_runtime"
        send(info)
        import cv2
        import numpy as np
        import torch
        info["stage"] = "load_models"
        send(info)
        from ai_service.config import Config
        from ai_service.models import LocalModels
        from ai_service.sources import CaptureLink, CapturedFrame
        from ai_service.safety_candidates import SafetyCandidateCollector
        from ai_service.types import Sample
        config = Config.load(os.environ.get("SAFETY_MODEL_CONFIG") or str(ROOT / "ai-service/config.camera-gpu.json"))
        if config.models.device != "cpu" and not torch.cuda.is_available():
            raise ValueError
        config.open_timeout_seconds, config.read_timeout_seconds = 8, 3
        models = LocalModels(config.models)
        info["stage"] = "warmup"
        send(info)
        models.infer(np.zeros((360, 640, 3), np.uint8))
        candidate_root = Path(os.environ.get("SAFETY_CANDIDATE_DIR") or
                              ROOT / "runtime/safety-candidates" / ("local_" + uuid4().hex))
        collector = SafetyCandidateCollector(config, candidate_root)
        engine = collector.engine
        candidates = {}
        pending_candidates = []
        from ai_service.recording import CameraRecorder
        recorder = CameraRecorder(config.recording, url)
        from ai_service.realtime import LatestFireWorker
        fire_worker = LatestFireWorker(models.infer_flames)
        recent_frames = deque(maxlen=40)
        recent_fire = deque(maxlen=20)
        visible_flames, flame_t, fire_ms = [], None, None
        from ai_service.preview_alerts import PreviewAlerts
        device_id = values.get("SAFETY_DEVICE_ID") or ""
        device_token = values.get("SAFETY_DEVICE_TOKEN") or ""
        if not device_id.strip() or not device_token.strip():
            # Phone delivery is optional. An intentionally unconfigured relay is
            # disabled, not a camera/runtime failure.
            info["alerts"] = {"enabled": False, "state": "disabled"}
        else:
            try:
                alerts = PreviewAlerts(config, ROOT / "runtime/web-reviewed-alerts-v1",
                    device_id,
                    values.get("SAFETY_BACKEND_URL") or "http://127.0.0.1:8010",
                    device_token)
                info["alerts"] = alerts.status()
            except Exception:
                # Keep local preview usable; never echo URL, token or exception text.
                info["alerts"] = {"enabled": False, "error": "alarm_relay_unavailable"}
        events = deque(maxlen=20)
        info.pop("stage", None)
        session = 0
        sequence = 0
        edges = [(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)]
        while not stop.is_set():
            if link is None:
                info["state"] = "connecting"
                send(info)
                link = CaptureLink(url, config)
                if not link.open(stop):
                    link.close(); link = None
                    info["state"] = "reconnecting"
                    send(info)
                    stop.wait(1)
                    continue
                session += 1
                collector.reset()
                visible_flames, flame_t = [], None
                recent_frames.clear()
                recent_fire.clear()
            kind, frame = link.read(stop)
            if stop.is_set():
                break
            if kind != "frame" or not isinstance(frame, CapturedFrame):
                if alerts:
                    alerts.disconnected()
                link.close(); link = None
                continue
            if time.perf_counter()-frame.t > config.read_timeout_seconds:
                continue
            sample = Sample(frame.image, frame.t, frame.occurred_at, session=session)
            t0 = time.perf_counter()
            fire_worker.submit(frame.image, frame.t, session)
            sample.poses = models.infer_pose(frame.image)
            inference_ms = (time.perf_counter()-t0)*1000
            observation = fire_worker.take(session, frame.t, config.fire.max_gap_seconds)
            sample.flames_observed = observation is not None
            if observation is not None:
                sample.flames, sample.flame_t = observation.flames, observation.t
                visible_flames, flame_t, fire_ms = observation.flames, observation.t, observation.inference_ms
                recent_fire.append(observation.t)
            if flame_t is None or not 0 <= frame.t-flame_t <= config.fire.max_gap_seconds:
                visible_flames = []
            if alerts:
                alerts.mark_frame()
            # A cloud request never blocks this capture/inference thread.
            pending_candidates = []
            for candidate in collector.process(sample):
                identifier = candidate["id"]
                entry = {"candidate_id": identifier, "event_type": candidate["event_type"],
                         "occurred_at": candidate["occurred_at"], "confidence": round(candidate["confidence"], 3),
                         "event_id": None, "review_status": "pending"}
                events.appendleft(entry)
                candidates[identifier] = (candidate, entry)
                pending_candidates.append(identifier)
                # Bound state if a parent dies or stops accepting review results.
                if len(candidates) > 32:
                    _, old = candidates.pop(next(iter(candidates)))
                    old.update(review_status="unavailable", review_reason="复核等待队列超限，未确认报警。")
            while True:
                try:
                    result = review_results.get_nowait()
                except queue.Empty:
                    break
                stored = candidates.pop(result.get("candidate_id"), None)
                if stored is None:
                    continue
                candidate, entry = stored
                info["cloud_calls"] = info["cloud_calls"] or bool(result.get("cloud_images"))
                try:
                    apply_review_result(candidate, entry, result, alerts)
                    if alerts:
                        info["alerts"].pop("error", None)
                except Exception:
                    if alerts:
                        info["alerts"]["error"] = "alarm_queue_failed"
            # Resize once before drawing, avoiding a full-HD copy and full-HD overlays.
            source_height, source_width = frame.image.shape[:2]
            preview_width = min(1280, source_width)
            preview_height = max(1, round(source_height*preview_width/source_width))
            image = cv2.resize(frame.image, (preview_width, preview_height))
            scale = np.array([preview_width/source_width, preview_height/source_height])
            for pose in sample.poses:
                x1,y1,x2,y2 = (np.asarray(pose.bbox)*np.tile(scale, 2)).astype(int)
                cv2.rectangle(image, (x1,y1), (x2,y2), (80,230,80), 2)
                for a,b in edges:
                    if pose.keypoints[a,2] >= .55 and pose.keypoints[b,2] >= .55:
                        cv2.line(image, tuple((pose.keypoints[a,:2]*scale).astype(int)), tuple((pose.keypoints[b,:2]*scale).astype(int)), (255,200,0), 2)
                for x,y,c in pose.keypoints:
                    if c >= .55:
                        cv2.circle(image, (int(x*scale[0]),int(y*scale[1])), 3, (0,230,255), -1)
            for flame in visible_flames:
                x1,y1,x2,y2 = (np.asarray(flame.bbox)*np.tile(scale, 2)).astype(int)
                cv2.rectangle(image,(x1,y1),(x2,y2),(0,80,255),2)
                cv2.putText(image,"FIRE CANDIDATE",(max(0,x1),max(15,y1)),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,80,255),1)
            sequence += 1
            cv2.putText(image,"LIVE / LOCAL AI",(10,22),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),2)
            ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if not ok:
                raise RuntimeError
            recent_frames.append(time.perf_counter())
            fps = (len(recent_frames)-1)/(recent_frames[-1]-recent_frames[0]) if len(recent_frames)>1 else 0
            fire_fps = (len(recent_fire)-1)/(recent_fire[-1]-recent_fire[0]) if len(recent_fire)>1 else 0
            info.update(state="running", frames=sequence, persons=len(sample.poses), fire_candidates=len(visible_flames),
                width=source_width, height=source_height, preview_width=image.shape[1], preview_height=image.shape[0],
                flame_tiling_enabled=config.models.flame_tiling_enabled, inference_ms=round(inference_ms,1),
                fps=round(fps,1), fire_fps=round(fire_fps,1), fire_inference_ms=round(fire_ms,1) if fire_ms is not None else None,
                pending_candidates=pending_candidates,
                fire_state=("error" if fire_worker.failed else "starting" if flame_t is None else
                            "stale" if frame.t-flame_t > config.fire.max_gap_seconds else "running"), events=list(events),
                fall_diagnostics=list(engine.fall.diagnostics.values()),
                help_facing_camera_required=config.help.require_facing_camera,
                help_face_candidates=sum(engine.help.facing_camera_confidence(p) is not None for p in sample.poses)
                    if config.help.require_facing_camera else None,
                device=("cpu" if config.models.device == "cpu" else "cuda:"+config.models.device), occurred_at=frame.occurred_at.isoformat(),
                decoded_frame_age_ms=round((time.perf_counter()-frame.t)*1000,1))
            if alerts:
                previous_error = info["alerts"].get("error")
                info["alerts"] = alerts.status()
                if previous_error:
                    info["alerts"]["error"] = previous_error
            send(info, jpeg.tobytes())
            # Preview is capped independently of camera decode to limit CPU/bandwidth.
            stop.wait(max(0, .05-(time.perf_counter()-t0)))
    except (Exception, KeyboardInterrupt):
        info.update(state="error", error="camera_preview_failed")
        try:
            send(info)
        except (OSError, BrokenPipeError):
            pass
    finally:
        stop.set()
        if link is not None:
            link.close()
        if recorder is not None:
            recorder.close()
        if fire_worker is not None:
            fire_worker.close()
        if alerts is not None:
            alerts.close()
        try:
            if info.get("state") != "error":
                info["state"] = "stopped"
                send(info)
        except (OSError, BrokenPipeError):
            pass
        output.close()


if __name__ == "__main__":
    main()

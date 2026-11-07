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

PREVIEW_INTERVAL = .05
POSE_OVERLAY_MAX_AGE = .25
POSE_EDGES = [(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)]

def preview_jpeg(frame, poses, flames):
    import cv2
    import numpy as np

    height, width = frame.image.shape[:2]
    preview_width = min(1280, width)
    preview_height = max(1, round(height * preview_width / width))
    image = cv2.resize(frame.image, (preview_width, preview_height))
    scale = np.array([preview_width / width, preview_height / height])
    for pose in poses:
        x1,y1,x2,y2 = (np.asarray(pose.bbox)*np.tile(scale, 2)).astype(int)
        cv2.rectangle(image, (x1,y1), (x2,y2), (80,230,80), 2)
        for a,b in POSE_EDGES:
            if pose.keypoints[a,2] >= .55 and pose.keypoints[b,2] >= .55:
                cv2.line(image, tuple((pose.keypoints[a,:2]*scale).astype(int)), tuple((pose.keypoints[b,:2]*scale).astype(int)), (255,200,0), 2)
        for x,y,c in pose.keypoints:
            if c >= .55:
                cv2.circle(image, (int(x*scale[0]),int(y*scale[1])), 3, (0,230,255), -1)
    for flame in flames:
        x1,y1,x2,y2 = (np.asarray(flame.bbox)*np.tile(scale, 2)).astype(int)
        cv2.rectangle(image,(x1,y1),(x2,y2),(0,80,255),2)
        cv2.putText(image,"FIRE CANDIDATE",(max(0,x1),max(15,y1)),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,80,255),1)
    cv2.putText(image,"LIVE / LOCAL AI",(10,22),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),2)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        raise RuntimeError("preview JPEG encoding failed")
    return preview_width, preview_height, jpeg.tobytes()


def frame_rate(times):
    return round((len(times)-1)/(times[-1]-times[0]), 1) if len(times) > 1 else None


def apply_review_result(candidate, entry, result, alerts):
    """Only a successful, matching cloud review may enter the phone alarm outbox."""
    verdict = result.get("verdict")
    if (result.get("candidate_id") != candidate["id"] or
            verdict not in {"confirmed", "rejected", "uncertain", "unavailable", "duplicate"} or
            (verdict == "confirmed" and (result.get("cloud_images") is not True or not result.get("model")))):
        verdict = "unavailable"
    entry.update(review_status=verdict, review_reason=str(result.get("reason", ""))[:800],
                 review_model=result.get("model"))
    if verdict != "confirmed" or alerts is None:
        return
    from ai_service.types import Signal
    sys.path.insert(0, str(ROOT.parent))
    from backend.alert_evidence import POLICY, select_frame
    selected, phase = select_frame(candidate, result.get("frame_ids") or [])
    reviewed_signal = Signal(candidate["event_type"], candidate["track_id"], candidate["confidence"],
        {"review_status": "confirmed", "review_model": result["model"], "candidate_id": candidate["id"],
         "cloud_images": True, "review_reason": entry["review_reason"],
         "evidence_phase": phase, "evidence_selection_policy": POLICY,
         "evidence_offset_ms": selected.get("pts_ms") if selected else None})
    keyframe = Path(selected['path']) if selected is not None else None
    when = datetime.fromisoformat(candidate["occurred_at"])
    entry["event_id"] = (alerts.alarm(reviewed_signal, when, keyframe=keyframe)
                         if keyframe is not None else alerts.alarm(reviewed_signal, when))


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
    output_lock = threading.Lock()
    ready = threading.Condition()
    latest_frame = overlay = None

    def send(info, jpeg=b""):
        packet = dict(info, jpeg_size=len(jpeg))
        if recorder is not None and packet.get("kind") != "analysis":
            recording_status = recorder.status()
            packet["recording_status"] = recording_status
            packet["recording"] = recording_status["state"] == "recording"
        data = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()
        with output_lock:
            if jpeg:
                # Do not publish a JPEG that finished encoding after its capture
                # session disconnected or changed.
                with ready:
                    if latest_frame is None or latest_frame[0] != info["capture_session"]:
                        return
            output.write(struct.pack("!I", len(data))+data+jpeg)
    info = {"state": "starting", "frames": 0, "events": [], "recording": False, "cloud_calls": False,
            "smoke_enabled": False, "sparks_enabled": False,
            "safety_pipeline": "small_models_then_qwen"}
    alerts = None
    fire_worker = None
    recorder = None
    capture_thread = None
    preview_thread = None
    stream_failed = threading.Event()
    try:
        info["stage"] = "config"
        send(info)
        from dotenv import dotenv_values
        from urllib.parse import urlsplit
        values = dotenv_values(ROOT / ".env")
        url = values.get("RTSP_URL") or ""
        # A phone-provided connection stays in a local ignored file, never argv.
        profile = os.environ.get("SAFETY_CAMERA_CONNECTION_FILE")
        if profile:
            url = json.loads(Path(profile).read_text(encoding="utf-8"))["rtsp_url"]
        parsed = urlsplit(url)
        if parsed.scheme not in ("rtsp", "rtsps") or not parsed.hostname or not parsed.username or not parsed.password:
            raise ValueError
        from ai_service.runtime_env import configure_model_runtime
        configure_model_runtime()
        info["stage"] = "import_runtime"
        send(info)
        import numpy as np
        import torch
        info["stage"] = "load_models"
        send(info)
        from ai_service.config import Config
        from ai_service.models import LocalModels
        from ai_service.sources import CaptureLink, CapturedFrame
        from ai_service.ffmpeg_capture import FFmpegCaptureLink
        from ai_service.safety_candidates import SafetyCandidateCollector
        from ai_service.types import Sample
        config = Config.load(os.environ.get("SAFETY_MODEL_CONFIG") or str(ROOT / "ai-service/config.camera-gpu.json"))
        if config.models.device != "cpu" and not torch.cuda.is_available():
            raise ValueError
        config.open_timeout_seconds, config.read_timeout_seconds = 8, 3
        capture_factory = FFmpegCaptureLink if config.recording.ffmpeg_path else CaptureLink
        info["capture_backend"] = "ffmpeg" if config.recording.ffmpeg_path else "opencv"
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
        pose_sample_gaps = deque(maxlen=12)
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
        info.pop("state", None)
        info.pop("recording", None)
        def capture_frames():
            nonlocal latest_frame, overlay
            session = 0
            try:
                while not stop.is_set():
                    send({"kind": "connection", "state": "connecting"})
                    link = capture_factory(url, config)
                    try:
                        if link.open(stop):
                            session += 1
                            capture_times = deque(maxlen=40)
                            while not stop.is_set():
                                kind, frame = link.read(stop)
                                if stop.is_set() or kind != "frame" or not isinstance(frame, CapturedFrame):
                                    break
                                started = time.perf_counter()
                                if capture_times and (frame.sequence <= capture_times[-1][0] or frame.t <= capture_times[-1][1]):
                                    break
                                if started-frame.t > config.read_timeout_seconds:
                                    continue
                                capture_times.append((frame.sequence, frame.t))
                                source_fps = None
                                if len(capture_times) > 1:
                                    first_sequence, first_t = capture_times[0]
                                    source_fps = round((frame.sequence-first_sequence)/(frame.t-first_t), 1)
                                # Each consumer reads the newest frame at its own
                                # pace. Encoding a preview cannot delay this handoff.
                                with ready:
                                    latest_frame = (session, frame, source_fps)
                                    ready.notify_all()
                                stop.wait(max(0, PREVIEW_INTERVAL-(time.perf_counter()-started)))
                    finally:
                        with ready:
                            latest_frame = overlay = None
                            if alerts:
                                alerts.disconnected()
                        link.close()
                    if not stop.is_set():
                        send({"kind": "connection", "state": "reconnecting"})
                        stop.wait(1)
            except Exception:
                stream_failed.set()
                stop.set()
            finally:
                with ready:
                    ready.notify_all()

        def publish_preview():
            import cv2

            last_frame = session = None
            preview_times = deque(maxlen=40)
            try:
                while not stop.is_set():
                    with ready:
                        ready.wait_for(lambda: stop.is_set() or (latest_frame is not None and latest_frame is not last_frame), .2)
                        if stop.is_set():
                            return
                        if latest_frame is None or latest_frame is last_frame:
                            continue
                        last_frame, current_overlay = latest_frame, overlay
                    started = time.perf_counter()
                    next_session, frame, source_fps = last_frame
                    if next_session != session:
                        session = next_session
                        preview_times.clear()
                    poses, flames = (), ()
                    if current_overlay is not None and current_overlay[0] == frame.image.shape[:2]:
                        _, pose_t, detected_poses, flame_t, detected_flames = current_overlay
                        if 0 <= frame.t-pose_t <= POSE_OVERLAY_MAX_AGE:
                            poses = detected_poses
                        if flame_t is not None and 0 <= frame.t-flame_t <= config.fire.max_gap_seconds:
                            flames = detected_flames
                    try:
                        preview_width, preview_height, jpeg = preview_jpeg(frame, poses, flames)
                    except (cv2.error, RuntimeError):
                        stop.wait(PREVIEW_INTERVAL)
                        continue  # A failed JPEG must not stop pose analysis.
                    preview_times.append(time.perf_counter())
                    height, width = frame.image.shape[:2]
                    send(dict(kind="preview", capture_session=session, state="running",
                        width=width, height=height, preview_width=preview_width, preview_height=preview_height,
                        source_fps=source_fps, preview_fps=frame_rate(preview_times),
                        occurred_at=frame.occurred_at.isoformat(),
                        decoded_frame_age_ms=round((time.perf_counter()-frame.t)*1000,1)), jpeg)
                    stop.wait(max(0, PREVIEW_INTERVAL-(time.perf_counter()-started)))
            except Exception:
                stream_failed.set()
                stop.set()

        capture_thread = threading.Thread(target=capture_frames, name="camera-capture", daemon=True)
        capture_thread.start()
        preview_thread = threading.Thread(target=publish_preview, name="camera-preview", daemon=True)
        preview_thread.start()
        session = last_frame = None
        sequence = 0
        while not stop.is_set():
            with ready:
                ready.wait_for(lambda: stop.is_set() or (latest_frame is not None and latest_frame is not last_frame), .2)
                if stop.is_set():
                    break
                if latest_frame is None or latest_frame is last_frame:
                    continue
                last_frame = latest_frame
            next_session, frame, _ = last_frame
            if session != next_session:
                session = next_session
                collector.reset()
                visible_flames, flame_t, fire_ms = [], None, None
                recent_frames.clear()
                pose_sample_gaps.clear()
                recent_fire.clear()
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
            with ready:
                if latest_frame is not None and latest_frame[0] == session and not stop.is_set():
                    overlay = (frame.image.shape[:2], frame.t, sample.poses, flame_t, visible_flames)
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
            sequence += 1
            if recent_frames:
                pose_sample_gaps.append(frame.t-recent_frames[-1])
            recent_frames.append(frame.t)
            info.update(kind="analysis", capture_session=session, frames=sequence,
                persons=len(sample.poses), fire_candidates=len(visible_flames),
                pose_enabled=models.pose is not None,
                pose_sample_gap_ms=round(max(pose_sample_gaps)*1000,1) if pose_sample_gaps else None,
                flame_tiling_enabled=config.models.flame_tiling_enabled, inference_ms=round(inference_ms,1),
                fps=frame_rate(recent_frames), fire_fps=frame_rate(recent_fire), fire_inference_ms=round(fire_ms,1) if fire_ms is not None else None,
                pending_candidates=pending_candidates,
                fire_state=("error" if fire_worker.failed else "starting" if flame_t is None else
                            "stale" if frame.t-flame_t > config.fire.max_gap_seconds else "running"), events=list(events),
                fall_diagnostics=list(engine.fall.diagnostics.values()),
                help_facing_camera_required=config.help.require_facing_camera,
                help_face_candidates=sum(engine.help.facing_camera_confidence(p) is not None for p in sample.poses)
                    if config.help.require_facing_camera else None,
                device=("cpu" if config.models.device == "cpu" else "cuda:"+config.models.device),
                analysis_frame_age_ms=round((time.perf_counter()-frame.t)*1000,1))
            if alerts:
                previous_error = info["alerts"].get("error")
                info["alerts"] = alerts.status()
                if previous_error:
                    info["alerts"]["error"] = previous_error
            send(info)
            stop.wait(max(0, PREVIEW_INTERVAL-(time.perf_counter()-t0)))
        if stream_failed.is_set():
            raise RuntimeError("camera capture or preview failed")
    except (Exception, KeyboardInterrupt):
        info.update(state="error", error="camera_preview_failed")
    finally:
        stop.set()
        if capture_thread is not None:
            capture_thread.join()
        if preview_thread is not None:
            preview_thread.join()
        if recorder is not None:
            recorder.close()
        if fire_worker is not None:
            fire_worker.close()
        if alerts is not None:
            alerts.close()
        try:
            state = "error" if info.get("state") == "error" else "stopped"
            send(dict(kind="connection", state=state, **({"error": info["error"]} if state == "error" else {})))
        except (OSError, BrokenPipeError):
            pass
        output.close()


if __name__ == "__main__":
    main()

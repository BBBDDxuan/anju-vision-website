"""Loopback-only, opt-in live camera preview. No camera secrets reach HTTP responses."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import ipaddress
import json
import os
from pathlib import Path
import struct
import subprocess
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from .errors import AppError

ROOT = Path(__file__).resolve().parents[1]
MAX_PACKET = 2_000_000
MAX_METADATA = 128_000


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        part = stream.read(size-len(data))
        if not part:
            raise EOFError
        data.extend(part)
    return bytes(data)


class LiveCamera:
    def __init__(self, root=ROOT, safety=None):
        self.root = Path(root)
        self.safety = safety
        self.review_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-safety-review")
        self.review_slots = threading.BoundedSemaphore(8)
        self.candidate_root = None
        self.review_session = None
        self.review_seen = set()
        self.review_error = None
        self.lock = threading.RLock()
        self.process = self.thread = None
        self.frame = None
        self.received = None
        self.info = {"state": "stopped", "frames": 0, "events": [], "recording": False, "cloud_calls": False}

    def _review_status(self):
        available, error = False, None
        try:
            if self.safety is None:
                raise AppError("SAFETY_REVIEW_DISABLED", "复核未配置。", 503)
            self.safety.ready()
            available = True
        except AppError as exc:
            error = exc.code
        return {"safety_pipeline": "small_models_then_qwen",
                "review_enabled": bool(self.safety and self.safety.enabled),
                "review_model": self.safety.model if self.safety else None,
                "cloud_calls": available, "review_error": self.review_error or error}

    def _send(self, message):
        if self.process and self.process.poll() is None and self.process.stdin:
            try:
                self.process.stdin.write(message+b"\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return self.status()
            self.frame, self.received = None, None
            project = self.root / "elder-safety"
            python = Path(self.safety.settings.safety_python) if self.safety else project / ".venv-gpu/Scripts/python.exe"
            venv = python.parent.parent / "pyvenv.cfg"
            script = project / "scripts/web-camera-worker.py"
            if not all(p.is_file() for p in (python, script, project / ".env")) or (self.safety is None and not venv.is_file()):
                raise AppError("CAMERA_RUNTIME_MISSING", "本地摄像头配置或 GPU 环境不存在。", 503)
            home = (next((line.split("=",1)[1].strip() for line in venv.read_text(encoding="utf-8-sig").splitlines() if line.startswith("home =")), "")
                    if venv.is_file() else str(python.parent))
            if not home or not (Path(home)/"python.exe").is_file():
                raise AppError("CAMERA_RUNTIME_MISSING", "GPU 基础环境不可用。", 503)
            env = os.environ.copy()
            # Parent web .env must not override the separate camera config.
            for key in ("RTSP_URL", "SAFETY_DEVICE_TOKEN", "DASHSCOPE_API_KEY"):
                env.pop(key, None)
            env["PATH"] = os.pathsep.join([home, str(Path(home)/"Library/bin"), str(Path(home)/"Scripts"), env.get("PATH", "")])
            env["PYTHONUTF8"] = "1"
            self.review_error = None
            self.review_session = "live_" + uuid4().hex
            self.review_seen = set()
            self.candidate_root = project / "runtime/safety-candidates" / self.review_session
            env["SAFETY_CANDIDATE_DIR"] = str(self.candidate_root)
            if self.safety is not None:
                env["SAFETY_MODEL_CONFIG"] = str(self.safety.settings.safety_model_config)
            # The GPU worker never receives cloud credentials. Only this process reviews.
            for key in list(env):
                if any(marker in key.upper() for marker in ("API_KEY", "VLM_")):
                    env.pop(key, None)
            self.info = {"state": "starting", "frames": 0, "events": [], "recording": False, "cloud_calls": False}
            self.process = subprocess.Popen([str(python), "-u", str(script)], cwd=project, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.thread = threading.Thread(target=self._read, args=(self.process,), daemon=True, name="camera-preview-reader")
            self.thread.start()
            return dict(self.info)

    def _read(self, process):
        try:
            while True:
                size = struct.unpack("!I", read_exact(process.stdout, 4))[0]
                if not 1 <= size <= MAX_METADATA:
                    raise ValueError
                info = json.loads(read_exact(process.stdout, size))
                candidates = info.pop("pending_candidates", [])
                image_size = info.pop("jpeg_size", 0)
                if not isinstance(image_size, int) or not 0 <= image_size <= MAX_PACKET:
                    raise ValueError
                image = read_exact(process.stdout, image_size) if image_size else None
                with self.lock:
                    if self.process is not process:
                        break
                    info.update(self._review_status())
                    self.info = info
                    if image:
                        self.frame, self.received = image, time.monotonic()
                if isinstance(candidates, list) and len(candidates) <= 8:
                    for identifier in candidates:
                        self._queue_review(identifier, process)
        except (EOFError, OSError, ValueError, TypeError):
            pass
        finally:
            with self.lock:
                if self.process is process:
                    self.review_session = None
                    self.frame, self.received = None, None
                    if self.info.get("state") != "stopped":
                        self.info["recording"] = False
                        if isinstance(self.info.get("recording_status"), dict):
                            self.info["recording_status"] = dict(self.info["recording_status"], state="error", error="recording_worker_stopped")
                    if self.info.get("state") not in ("stopped", "error"):
                        self.info["state"] = "error"
                        self.info["error"] = "camera_worker_stopped"

    def _queue_review(self, identifier, process):
        with self.lock:
            if (self.process is not process or self.review_session is None
                    or not isinstance(identifier, str) or identifier in self.review_seen):
                return
            self.review_seen.add(identifier)
            root, session = self.candidate_root, self.review_session
            if not self.review_slots.acquire(blocking=False):
                self._send(json.dumps({"candidate_id": identifier, "verdict": "unavailable",
                                      "reason": "复核队列已满，候选未确认。"}, ensure_ascii=False).encode())
                return
        def run():
            result = {"candidate_id": identifier, "verdict": "unavailable", "reason": "安全复核不可用，候选未确认。"}
            try:
                with self.lock:
                    if self.process is not process or self.review_session != session:
                        return
                if self.safety is None or root is None:
                    raise AppError("SAFETY_REVIEW_DISABLED", "安全复核未配置。", 503)
                candidate = self.safety.load_candidate(identifier, root)
                result = self.safety.review(candidate, session, session)
                # Store audit metadata locally; never expose evidence paths or API usage to HTTP status.
                from .hybrid_safety import atomic_json
                atomic_json(Path(root)/identifier/"review.json", result)
            except AppError as exc:
                result.update(reason=exc.message, error=exc.code)
            except Exception:
                result.update(reason="安全复核处理失败，候选未确认。", error="SAFETY_REVIEW_FAILED")
            finally:
                self.review_slots.release()
            public = {k: result[k] for k in ("candidate_id", "verdict", "reason", "model", "error", "cloud_images") if k in result}
            with self.lock:
                if self.process is process and self.review_session == session:
                    self.review_error = result.get("error")
                    self._send(json.dumps(public, ensure_ascii=False).encode())
        self.review_pool.submit(run)

    def close(self):
        self.stop()
        self.review_pool.shutdown(wait=True, cancel_futures=False)

    def status(self):
        with self.lock:
            self._send(b"ping")
            result = dict(self.info)
            result.update(self._review_status())
            age = time.monotonic()-self.received if self.received is not None else None
            result["frame_age_seconds"] = round(age, 3) if age is not None else None
            if result.get("state") == "running" and (age is None or age > 3):
                result["state"] = "stale"
            return result

    def image(self):
        with self.lock:
            if self.info.get("state") != "running" or self.frame is None or self.received is None or time.monotonic()-self.received > 3:
                raise AppError("CAMERA_FRAME_UNAVAILABLE", "画面未就绪或已过期，请检查摄像头连接。", 503, True)
            return self.frame

    def stop(self):
        with self.lock:
            process = self.process
            self._send(b"stop")
            self.frame, self.received = None, None
            self.info["state"] = "stopped"
            self.info["recording"] = False
            self.review_session = None
        if process:
            try:
                process.wait(timeout=14)
            except subprocess.TimeoutExpired:
                # Only this explicitly owned worker tree; no other Python processes.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                else:
                    process.terminate()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        with self.lock:
            self.process = None
            self.frame, self.received = None, None
            self.info["state"] = "stopped"
            self.info["recording"] = False
            if isinstance(self.info.get("recording_status"), dict):
                self.info["recording_status"] = dict(self.info["recording_status"], state="stopped")
            return dict(self.info)


def camera_router(controller, cloud_mode):
    router = APIRouter(prefix="/api/camera")
    def allowed(request):
        # Never expose a private home camera from an existing cloud deployment.
        host = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if cloud_mode or not local:
            raise AppError("CAMERA_LOCAL_ONLY", "实时摄像头仅允许在本机网页使用。", 403)
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise AppError("CAMERA_ORIGIN_REJECTED", "请从本机同源网页操作摄像头。", 403)
    @router.get("/status")
    def status(request: Request):
        allowed(request)
        return JSONResponse(controller.status(), headers={"Cache-Control": "no-store"})
    @router.post("/start")
    def start(request: Request):
        allowed(request)
        return controller.start()
    @router.post("/stop")
    def stop(request: Request):
        allowed(request)
        return controller.stop()
    @router.get("/frame.jpg")
    def frame(request: Request):
        allowed(request)
        return Response(controller.image(), media_type="image/jpeg", headers={"Cache-Control": "no-store, max-age=0", "X-Content-Type-Options": "nosniff"})
    return router

"""Authenticated phone control of the single shared backend camera worker."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import re
from urllib.parse import quote, urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .errors import AppError


class CameraConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    host: str = Field(max_length=15)
    port: int = Field(default=554, ge=1, le=65535, strict=True)
    username: SecretStr = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)
    path: str = Field(default="stream1", min_length=1, max_length=256)

    @field_validator("host")
    @classmethod
    def private_ipv4(cls, value):
        address = ipaddress.IPv4Address(value)
        if not any(address in ipaddress.IPv4Network(block) for block in
                   ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
            raise ValueError("Camera must use a private IPv4 address")
        return str(address)

    @field_validator("username", "password")
    @classmethod
    def credentials(cls, value):
        text = value.get_secret_value()
        if not text.strip() or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError("Invalid camera credentials")
        return value

    @field_validator("path")
    @classmethod
    def stream_path(cls, value):
        if (not value.lstrip("/") or "://" in value or ".." in value
                or any(char.isspace() or ord(char) < 32 or char in "#\\" for char in value)):
            raise ValueError("Invalid camera path")
        return value.lstrip("/")

    def profile(self):
        user = quote(self.username.get_secret_value(), safe="")
        password = quote(self.password.get_secret_value(), safe="")
        return {"host": self.host, "port": self.port,
                "rtsp_url": f"rtsp://{user}:{password}@{self.host}:{self.port}/{self.path}"}


def probe_camera(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError:
        raise AppError("CAMERA_UNREACHABLE", "电脑后端无法连接摄像头，请检查电脑到摄像头的网络和 RTSP 端口。", 503, True) from None



def probe_stream(url, ffprobe):
    """Check RTSP authentication/media before saving a profile or starting models.

    Native diagnostics may contain the full credential URL. Only fixed error
    codes/messages leave this function; never attach subprocess exceptions.
    """
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-rtsp_transport", "tcp",
             "-timeout", "5000000", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type,width,height", "-of", "json", url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise AppError("CAMERA_STREAM_TIMEOUT", "摄像头取流超时，请检查设备是否在线及取流连接数。", 503, True) from None
    except OSError:
        raise AppError("CAMERA_PROBE_MISSING", "后端取流检查工具不可用，请检查 FFprobe 配置。", 503) from None
    if result.returncode:
        diagnostic = result.stderr.decode("utf-8", errors="replace")
        if re.search(r"\b(?:401|403)\b", diagnostic):
            raise AppError("CAMERA_AUTH_FAILED", "摄像头拒绝了本地设备账号或密码，请核对摄像头本地账号及 RTSP 权限。", 422) from None
        if re.search(r"\b404\b", diagnostic):
            raise AppError("CAMERA_STREAM_NOT_FOUND", "摄像头视频路径不存在，请核对 stream1、stream2 或通道参数。", 422) from None
        raise AppError("CAMERA_STREAM_UNAVAILABLE", "摄像头端口可达但无法取流，请检查 RTSP 服务、视频路径及连接数。", 503, True) from None
    try:
        streams = json.loads(result.stdout).get("streams", [])
        valid = any(item.get("codec_type") == "video" and item.get("width", 0) > 0
                    and item.get("height", 0) > 0 for item in streams)
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise AppError("CAMERA_STREAM_UNAVAILABLE", "摄像头没有返回有效的视频轨道，请检查视频路径和设备设置。", 503, True)


def require_alerting(controller):
    if controller.safety is None:
        raise AppError("SAFETY_REVIEW_DISABLED", "请先配置后端安全复核服务。", 503)
    controller.safety.ready()
    values = dotenv_values(controller.root / "elder-safety/.env")
    if not all((values.get(key) or "").strip() for key in ("SAFETY_DEVICE_ID", "SAFETY_DEVICE_TOKEN")):
        raise AppError("CAMERA_ALERT_RELAY_MISSING", "请先运行手机服务配置，设置摄像头告警设备身份。", 503)


def monitoring_router(controller, ffprobe="ffprobe"):
    router = APIRouter(prefix="/api/monitoring/camera")
    profile_file = controller.root / "runtime/camera-connection.json"

    def allowed(request):
        # This router is installed only on the authenticated LAN phone gateway.
        # Native clients have no Origin; browser mutations must be same-origin.
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise AppError("CAMERA_ORIGIN_REJECTED", "请从同源服务操作摄像头监护。", 403)

    def read_profile():
        try:
            return json.loads(profile_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def response():
        with controller.control_lock:
            result = controller.status()
            saved = read_profile()
            result.update(lifetime="backend", configured=saved is not None,
                          monitoring=bool(controller.process and controller.process.poll() is None),
                          camera={key: saved.get(key) for key in ("host", "port")} if saved else None)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.get("")
    def status(request: Request):
        allowed(request)
        return response()

    @router.post("")
    def connect(body: CameraConnection, request: Request):
        allowed(request)
        new = body.profile()
        with controller.control_lock:
            old = read_profile()
            require_alerting(controller)
            if (old == new and controller.process and controller.process.poll() is None
                    and controller.status().get("state") == "running"):
                return response()
            # A failed submission must not stop an existing working camera.
            probe_camera(body.host, body.port)
            probe_stream(new["rtsp_url"], ffprobe)
            profile_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = profile_file.with_name("camera-" + uuid4().hex + ".tmp")
            try:
                descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(new, stream)
                controller.stop()
                temporary.replace(profile_file)
                try:
                    controller.start()
                except Exception:
                    if old is None:
                        profile_file.unlink(missing_ok=True)
                    else:
                        # Roll back the private profile if the runtime cannot start.
                        temporary.write_text(json.dumps(old), encoding="utf-8")
                        temporary.replace(profile_file)
                    raise
            finally:
                temporary.unlink(missing_ok=True)
            return response()

    @router.post("/start")
    def start(request: Request):
        allowed(request)
        with controller.control_lock:
            require_alerting(controller)
            if (controller.process and controller.process.poll() is None
                    and controller.status().get("state") == "running"):
                return response()
            profile = read_profile()
            url = profile.get("rtsp_url", "") if profile else (dotenv_values(
                controller.root / "elder-safety/.env").get("RTSP_URL") or "")
            parsed = urlsplit(url)
            if not parsed.hostname:
                raise AppError("CAMERA_STREAM_UNAVAILABLE", "请先配置摄像头连接。", 422)
            CameraConnection.private_ipv4(parsed.hostname)
            probe_camera(parsed.hostname, parsed.port or 554)
            probe_stream(url, ffprobe)
            controller.start()  # Saved phone profile, or the existing local .env.
            return response()

    @router.post("/stop")
    def stop(request: Request):
        allowed(request)
        with controller.control_lock:
            controller.stop()
            return response()

    return router

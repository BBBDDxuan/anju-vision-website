"""Local discovery and short-lived, single-use phone pairing."""
import hmac
import ipaddress
import os
import re
import secrets
import socket
import threading
import time
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from .camera_monitoring import CameraConnection
from .errors import AppError

PAIR_EXCHANGE = "/api/lan/pair/exchange"


def local_peer(request):
    return request.client is not None and request.client.host in {"127.0.0.1", "::1"}


def discover_cameras():
    """Only return the IPv4 sender of a matching ONVIF discovery response."""
    message = f"urn:uuid:{uuid4()}"
    probe = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
      xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
      xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
      xmlns:n="http://www.onvif.org/ver10/network/wsdl">
      <s:Header><a:MessageID>{message}</a:MessageID>
      <a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
      <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
      </s:Header><s:Body><d:Probe><d:Types>n:NetworkVideoTransmitter</d:Types></d:Probe></s:Body></s:Envelope>"""
    hosts = set()
    deadline = time.monotonic() + 2.5
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        address = os.getenv("ANJU_PHONE_ADDRESS")
        if address:
            sock.bind((address, 0))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.sendto(probe.encode(), ("239.255.255.250", 3702))
        while len(hosts) < 32 and (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                payload, (host, _) = sock.recvfrom(16384)
            except TimeoutError:
                break
            if message.encode() not in payload or b"ProbeMatch" not in payload:
                continue
            try:
                hosts.add(CameraConnection.private_ipv4(host))
            except ValueError:
                continue
    return [{"host": host, "port": 554, "username": "admin", "path": "stream1"}
            for host in sorted(hosts, key=ipaddress.IPv4Address)]


class PairRequest(BaseModel):
    code: SecretStr = Field(min_length=32, max_length=64)


def lan_router(token, *, pairing=False):
    router = APIRouter(prefix="/api/lan")
    lock = threading.Lock()
    discovery_lock = threading.Lock()
    cached = []
    scanned_at = float("-inf")
    pending = None

    @router.get("/cameras")
    def cameras():
        nonlocal cached, scanned_at
        with discovery_lock:
            if time.monotonic() - scanned_at > 5:
                try:
                    cached = discover_cameras()
                except OSError:
                    raise AppError("DISCOVERY_UNAVAILABLE", "无法搜索局域网设备，请检查电脑网络或手动填写。", 503) from None
                scanned_at = time.monotonic()
            return JSONResponse({"cameras": cached}, headers={"Cache-Control": "no-store"})

    if not pairing:
        return router

    @router.post("/pair")
    def create_pair(request: Request):
        nonlocal pending
        origin = request.headers.get("origin")
        if not local_peer(request) or (origin and origin.rstrip("/") != str(request.base_url).rstrip("/")):
            raise AppError("PAIR_LOCAL_ONLY", "请在后端电脑上打开本机网页生成配对二维码。", 403)
        try:
            address = CameraConnection.private_ipv4(os.getenv("ANJU_PHONE_ADDRESS", ""))
            port = int(os.getenv("ANJU_PHONE_PORT", "8000"))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise AppError("PAIR_UNAVAILABLE", "请通过 start-phone.ps1 启动手机服务。", 503) from None
        code = secrets.token_urlsafe(32)
        with lock:
            pending = (code, time.monotonic() + 120)
        return JSONResponse({"kind": "anju-pair-v1", "origin": f"http://{address}:{port}", "code": code},
                            headers={"Cache-Control": "no-store"})

    @router.post("/pair/exchange")
    def exchange(body: PairRequest, request: Request):
        nonlocal pending
        try:
            CameraConnection.private_ipv4(request.client.host if request.client else "")
        except ValueError:
            raise AppError("PAIR_LAN_ONLY", "仅允许局域网手机配对。", 403) from None
        code = body.code.get_secret_value()
        with lock:
            if (request.headers.get("origin") or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code)
                    or not pending or time.monotonic() >= pending[1]
                    or not hmac.compare_digest(pending[0], code)):
                raise AppError("PAIR_INVALID", "配对码无效或已过期，请重新生成二维码。", 403)
            pending = None
        return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})

    return router

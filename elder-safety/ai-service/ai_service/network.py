from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
import time
from urllib.parse import quote, urlsplit

import httpx

from .config import Config
from .spool import Spool

log = logging.getLogger(__name__)


class DeliveryError(RuntimeError):
    """Sanitized category only; never the HTTP response/request text."""


def backend_url(value: str):
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("backend must be an http(s) origin without credentials/query/path")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("invalid backend port") from exc
    return value.rstrip("/")


class BackendClient:
    def __init__(self, backend: str, device_id: str, token: str, config: Config, transport=None):
        self.backend = backend_url(backend)
        self.device_id = device_id
        self.config = config
        if not token.strip() or "\n" in token or "\r" in token:
            raise ValueError("SAFETY_DEVICE_TOKEN must be nonempty and single-line")
        self.client = httpx.Client(headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=False, trust_env=False, transport=transport)

    def post(self, path, **kwargs):
        try:
            response = self.client.post(self.backend+path, **kwargs)
        except httpx.HTTPError:
            raise DeliveryError("transport_error") from None
        # 409 is only interpreted by send_event, with an exact event_id match.
        if response.status_code != 409 and not 200 <= response.status_code < 300:
            raise DeliveryError(f"http_{response.status_code}")
        return response

    @staticmethod
    def identifier(response, field):
        try:
            value = response.json()[field]
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError()
            return value
        except (ValueError, TypeError, KeyError):
            raise DeliveryError("invalid_response_identifier") from None

    def register(self):
        result = self.post("/api/v1/devices/register", json={"device_id": self.device_id,
                                                               "name": f"Safety camera {self.device_id}"[:100]})
        if result.status_code == 409:
            raise DeliveryError("registration_conflict")

    def heartbeat(self):
        result = self.post(f"/api/v1/devices/{quote(self.device_id, safe='')}/heartbeat", json={})
        if result.status_code == 409:
            raise DeliveryError("heartbeat_conflict")

    def upload(self, path: Path):
        path = Path(path)
        limit, mime = {".jpg": (self.config.evidence.max_jpg_bytes, "image/jpeg"),
                       ".mp4": (self.config.evidence.max_mp4_bytes, "video/mp4")}.get(path.suffix.lower(), (0, ""))
        if not path.is_file() or not 0 < path.stat().st_size <= limit:
            raise DeliveryError("evidence_missing_or_size_limit")
        with path.open("rb") as file:
            head = file.read(16)
            if (mime == "image/jpeg" and not head.startswith(b"\xff\xd8\xff")
                    or mime == "video/mp4" and head[4:8] != b"ftyp"):
                raise DeliveryError("invalid_evidence_signature")
            file.seek(0)
            result = self.post("/api/v1/evidence", data={"device_id": self.device_id},
                               files={"file": (path.name, file, mime)})
        if result.status_code == 409:
            raise DeliveryError("evidence_conflict")
        return self.identifier(result, "evidence_id")

    def send_event(self, payload):
        response = self.post("/api/v1/events", json=payload)
        canonical = self.identifier(response, "event_id")
        if canonical != payload["event_id"]:
            if response.json().get("deduplicated") is not True:
                raise DeliveryError("event_id_mismatch")
            # A canonical event can be returned by the backend dedup window.
            # Never accept a response explicitly identifying another device.
            if response.json().get("device_id", self.device_id) != self.device_id:
                raise DeliveryError("event_device_mismatch")
        return canonical

    def associate(self, event_id, field, evidence_id):
        if field not in ("evidence_id", "clip_evidence_id"):
            raise ValueError("invalid evidence association field")
        response = self.post(f"/api/v1/events/{quote(event_id, safe='')}/evidence",
                             json={field: evidence_id})
        if response.status_code == 409:
            raise DeliveryError("association_http_409")
        # The endpoint returns the updated event. Confirm the requested slot;
        # a 2xx without the association is not successful delivery.
        if (self.identifier(response, "event_id") != event_id
                or self.identifier(response, field) != evidence_id):
            raise DeliveryError("association_response_mismatch")

    def close(self):
        self.client.close()


class DeliveryWorker:
    def __init__(self, spool: Spool, client: BackendClient, config: Config, clock=time.time,
                 heartbeat_allowed=None):
        self.spool, self.client, self.config = spool, client, config
        self.clock = clock
        self.heartbeat_allowed = heartbeat_allowed or (lambda: True)
        self.registered = False
        self.next_register = 0.0
        self.register_attempts = 0
        self.next_heartbeat = 0.0
        self.stop_event = threading.Event()
        self.thread = None

    def backoff(self, attempts):
        return min(self.config.retry_max_seconds,
                   self.config.retry_initial_seconds * 2**min(attempts, 16))

    def tick(self):
        now = self.clock()
        if not self.registered:
            if not self.heartbeat_allowed() and not self.spool.pending_count():
                return
            if now < self.next_register:
                return
            try:
                self.client.register()
                self.registered = True
                self.register_attempts = 0
            except DeliveryError as exc:
                self.next_register = now+self.backoff(self.register_attempts)
                self.register_attempts += 1
                log.warning("registration deferred: %s", exc)
                return
        if now >= self.next_heartbeat and self.heartbeat_allowed():
            try:
                self.client.heartbeat()
            except DeliveryError as exc:
                log.warning("heartbeat deferred: %s", exc)
                if str(exc) in ("http_401", "http_403", "http_404"):
                    self.registered = False
                    self.next_register = now+self.config.retry_initial_seconds
            self.next_heartbeat = now+self.config.heartbeat_seconds
        job = self.spool.due(now)
        if job is None:
            return
        try:
            row = self.spool.event(job["event_id"])
            if job["kind"] == "alarm":
                if row["frozen_payload"]:
                    payload = json.loads(row["frozen_payload"])
                else:
                    payload = json.loads(row["payload"])
                    # A failed JPEG upload must not suppress the critical alert.
                    if row["keyframe"] and not row["evidence_id"]:
                        try:
                            evidence_id = self.client.upload(Path(row["keyframe"]))
                            self.spool.save_evidence(row["event_id"], "evidence_id", evidence_id)
                            row["evidence_id"] = evidence_id
                        except DeliveryError:
                            self._ensure_keyframe_retry(row)
                    if row["evidence_id"]:
                        payload["evidence_id"] = row["evidence_id"]
                    if row["clip_evidence_id"]:
                        payload["clip_evidence_id"] = row["clip_evidence_id"]
                    payload = self.spool.freeze_payload(row["event_id"], payload)
                canonical = self.client.send_event(payload)
                self.spool.acknowledge_alarm(job, canonical)
            else:
                is_keyframe = job["kind"] == "keyframe"
                field = "evidence_id" if is_keyframe else "clip_evidence_id"
                path_field = "keyframe" if is_keyframe else "clip_path"
                if not row[field]:
                    evidence_id = self.client.upload(Path(row[path_field]))
                    self.spool.save_evidence(row["event_id"], field, evidence_id)
                    row[field] = evidence_id
                # IDs are persisted before association; a timeout/restart retries
                # the same append, without uploading another copy.
                self.client.associate(row["backend_event_id"], field, row[field])
                self.spool.done(job)
        except (DeliveryError, OSError) as exc:
            # Never retain OSError text because paths may include private values.
            reason = str(exc) if isinstance(exc, DeliveryError) else "local_io_error"
            if job["kind"] != "alarm" and reason in ("association_http_409", "http_403", "http_404"):
                self.spool.block(job, reason)
                log.error("evidence association blocked (%s): %s; local evidence retained; manual review required",
                          job["kind"], reason)
            else:
                self.spool.retry(job, reason, self.backoff(job["attempts"]), now)
                log.warning("delivery deferred (%s): %s", job["kind"], reason)
            if reason in ("http_401", "http_403"):
                self.registered = False
                self.next_register = now+self.config.retry_initial_seconds

    def _ensure_keyframe_retry(self, row):
        with self.spool.lock, self.spool.db:
            self.spool.db.execute("INSERT OR IGNORE INTO jobs(event_id,kind) VALUES(?, 'keyframe')", (row["event_id"],))

    def start(self):
        def run():
            try:
                while not self.stop_event.is_set():
                    self.tick()
                    self.stop_event.wait(0.1)
            finally:
                self.client.close()
        self.thread = threading.Thread(target=run, name="delivery", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)
        return self.thread is None or not self.thread.is_alive()

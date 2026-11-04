"""Durable reviewed alarms with optional source JPEG evidence; no credentials in spool."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import uuid

from .config import Config
from .network import BackendClient, DeliveryWorker, backend_url
from .sources import SourceHealth
from .spool import Spool, SpoolFull
from .types import Signal


class PreviewAlerts:
    """Reuse the durable AI outbox; backend receipt is distinct from phone delivery."""

    def __init__(self, config: Config, state_dir: Path, device_id: str,
                 backend: str, token: str, *, transport=None, start=True):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", device_id):
            raise ValueError("invalid device identity")
        backend = backend_url(backend)
        # Validate secrets before creating persistent state. Never log their value.
        if len(token.strip()) < 24 or "REPLACE_" in token or "\n" in token or "\r" in token:
            raise ValueError("device credentials not configured")
        self.health = SourceHealth(max(1.0, config.read_timeout_seconds))
        client = BackendClient(backend, device_id, token, config, transport=transport)
        try:
            self.spool = Spool(state_dir, device_id, backend, config.max_spool_bytes)
        except Exception:
            client.close()
            raise
        self.device_id = device_id
        self.delivery = DeliveryWorker(self.spool, client, config, heartbeat_allowed=self.health.alive)
        if start:
            self.delivery.start()

    def mark_frame(self):
        self.health.mark_frame()

    def disconnected(self):
        self.health.clear()

    def alarm(self, signal: Signal, occurred_at: datetime, keyframe: Path | None = None):
        event_id = str(uuid.uuid4())
        owned_frame = None
        if keyframe is not None:
            try:
                keyframe = Path(keyframe)
                if keyframe.is_file() and keyframe.stat().st_size <= self.delivery.config.evidence.max_jpg_bytes:
                    content = keyframe.read_bytes()
                    if content.startswith(b'\xff\xd8\xff'):
                        self.spool.check_capacity(len(content) + 8192)
                        directory = self.spool.root / 'evidence'
                        directory.mkdir(exist_ok=True)
                        owned_frame = directory / (event_id + '.jpg')
                        owned_frame.write_bytes(content)
            except (OSError, SpoolFull):
                owned_frame = None
        self.spool.add_event({
            "event_id": event_id, "device_id": self.device_id,
            "event_type": signal.event_type, "severity": "critical",
            "confidence": signal.confidence, "occurred_at": occurred_at.isoformat(),
            "track_id": signal.track_id,
            "details": {"source_kind": "web_camera_preview", "simulated": False,
                        "evidence_available": False, "cloud_images": bool(signal.details.get("cloud_images", False)),
                        **({k: signal.details[k] for k in ("review_status", "review_model", "candidate_id", "review_reason")
                            if k in signal.details} if signal.details.get("review_status") == "confirmed" else {}),
                        "validation_scope": ("Qwen-reviewed visual candidate, not confirmed real-world danger"
                            if signal.details.get("review_status") == "confirmed" else "candidate alarm, not confirmed danger")},
        }, owned_frame)
        return event_id

    def status(self):
        return {"enabled": True, "pending": self.spool.pending_count(),
                "worker_alive": bool(self.delivery.thread and self.delivery.thread.is_alive()),
                "delivery_scope": "backend_receipt_only"}

    def close(self):
        self.health.clear()
        if self.delivery.thread is None:
            self.delivery.client.close()
        # A bounded HTTP call may still be finishing. Do not close its SQLite
        # connection under the worker; process exit will release it in that case.
        if self.delivery.stop():
            self.spool.close()

"""FCM HTTP v1 fan-out with durable per-token acceptance, not phone delivery.

No routes, no polling, no model requests. A owns the event outbox/retry loop.
Firebase credentials and registration tokens must never enter logs or receipts.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit

import httpx

SERVER = Path(__file__).resolve().parents[2]
CHANNEL = "elder_safety_alerts_v1"
ALERT_ID = re.compile(r"a_[0-9a-f]{40}\Z")


class PushError(RuntimeError):
    """Only static, non-sensitive error codes may cross the plugin boundary."""


@dataclass(frozen=True)
class Recipient:
    token_id: str
    token: str
    platform: str

    @property
    def key(self):
        return hashlib.sha256(self.token.encode()).hexdigest()


class AcceptanceLedger:
    """Only hashes and receipts; tokens and provider error bodies are not stored."""

    def __init__(self, filename):
        self.filename = Path(filename)
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS acceptance (alert_id TEXT, recipient TEXT, accepted_at REAL, PRIMARY KEY(alert_id, recipient))")

    def connect(self):
        c = sqlite3.connect(self.filename, timeout=1)
        c.execute("PRAGMA busy_timeout=1000")
        return c

    def accepted(self, identifier, recipient, *, contact_stage=None):
        # The base receipt also honors old ledgers and suppresses the same device
        # if it is registered under both roles (or moved between roles).
        stage_key = identifier + ':' + contact_stage if contact_stage else identifier
        with self.connect() as c:
            return c.execute("SELECT 1 FROM acceptance WHERE alert_id IN (?,?) AND recipient=?",
                             (identifier, stage_key, recipient)).fetchone() is not None

    def record(self, identifier, recipient, *, contact_stage=None):
        with self.connect() as c:
            now = time.time()
            c.execute("INSERT OR IGNORE INTO acceptance VALUES(?,?,?)", (identifier, recipient, now))
            if contact_stage:
                c.execute("INSERT OR IGNORE INTO acceptance VALUES(?,?,?)",
                          (identifier + ':' + contact_stage, recipient, now))


class SafetyRecipients:
    """Reuse A's actual registration table and preferences, not a second registry."""

    def __init__(self, repository):
        self.repository = repository

    def snapshot(self, contact_stage='primary'):
        if contact_stage not in {'primary', 'backup'}:
            raise PushError('INVALID_CONTACT_STAGE')
        prefs = self.repository.get_preferences()
        if prefs.get("notifications_enabled") is not True:
            return False, []
        result = {}
        for contact in prefs.get("contacts", []):
            name = contact.get("contact_id")
            if name != contact_stage:
                continue
            for row in self.repository.contact_tokens(name):
                if row.get("platform") not in {"android", "ios"}:
                    continue
                token = row.get("token")
                if not isinstance(token, str) or not token.strip() or len(token) > 4096:
                    continue
                recipient = Recipient(str(row["token_id"]), token, row["platform"])
                result[recipient.key] = recipient
        return True, list(result.values())


class ServiceAccountToken:
    """JWT signing locally; OAuth network uses cancellable, bounded async HTTP.

    Optional google-auth is loaded only for real Firebase builds. Never honor
    a credential-supplied token_uri or a user-supplied network destination.
    """

    def __init__(self, filename, client):
        try:
            from google.oauth2 import service_account
            self.credentials = service_account.Credentials.from_service_account_file(str(filename))
            self.project = self.credentials.project_id
            if not isinstance(self.project, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", self.project):
                raise ValueError()
        except Exception:
            raise PushError("FCM_CREDENTIALS_UNAVAILABLE") from None
        self.client = client
        self.cached = None
        self.expires = 0
        self.lock = asyncio.Lock()

    async def access_token(self):
        async with self.lock:
            if self.cached and time.time() < self.expires - 60:
                return self.cached
            try:
                from google.auth import jwt
                now = int(time.time())
                assertion = jwt.encode(self.credentials.signer, {
                    "iss": self.credentials.service_account_email,
                    "scope": "https://www.googleapis.com/auth/firebase.messaging",
                    "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600,
                }).decode("ascii")
                response = await self.client.post("https://oauth2.googleapis.com/token", data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
                response.raise_for_status()
                body = response.json()
                token, seconds = body["access_token"], int(body["expires_in"])
                if not isinstance(token, str) or not token or seconds < 60:
                    raise ValueError()
                self.cached, self.expires = token, time.time() + min(seconds, 3600)
                return token
            except asyncio.CancelledError:
                raise
            except Exception:
                raise PushError("FCM_AUTH_FAILED") from None


class FcmTransport:
    def __init__(self, client, credentials, *, scope):
        self.client, self.credentials, self.scope = client, credentials, scope

    async def send(self, recipient, alert):
        try:
            bearer = await self.credentials.access_token()
            identifier = alert["id"]
            message = {
                "token": recipient.token,
                "notification": {"title": "居家安全提醒", "body": "有新的已复核安全事件，请打开应用查看。"},
                "data": {"anju_type": "unified_alert", "alert_id": identifier,
                         "event_id": identifier, "source": alert["source"], "household": "default", "scope": self.scope,
                         "contact_stage": alert.get("contact_stage", "primary")},
                "android": {"priority": "HIGH", "ttl": "300s", "notification": {
                    "channel_id": CHANNEL, "tag": identifier, "sound": "default",
                    "visibility": "PRIVATE", "default_vibrate_timings": True}},
                "apns": {"headers": {"apns-push-type": "alert", "apns-priority": "10"},
                         "payload": {"aps": {"sound": "default"}}},
            }
            response = await self.client.post(
                f"https://fcm.googleapis.com/v1/projects/{self.credentials.project}/messages:send",
                headers={"Authorization": "Bearer " + bearer}, json={"message": message})
            if response.status_code != 200:
                raise PushError("FCM_PROVIDER_REJECTED")
            name = response.json().get("name")
            if not isinstance(name, str) or not name.startswith(f"projects/{self.credentials.project}/messages/"):
                raise PushError("FCM_INVALID_RECEIPT")
        except asyncio.CancelledError:
            raise
        except PushError:
            raise
        except Exception:
            raise PushError("FCM_SEND_FAILED") from None


class AlertPublisher:
    def __init__(self, recipients, transport, ledger, *, attempt_timeout=8, send_timeout=4, concurrency=4):
        self.recipients, self.transport, self.ledger = recipients, transport, ledger
        self.attempt_timeout, self.send_timeout, self.concurrency = attempt_timeout, send_timeout, concurrency
        self.lock = asyncio.Lock()

    async def publish(self, alert, *, idempotency_key):
        if (not isinstance(alert, dict) or not isinstance(alert.get("id"), str)
                or not ALERT_ID.fullmatch(alert["id"]) or idempotency_key != alert["id"]
                or alert.get("source") not in {"live_camera", "uploaded_recording"}
                or alert.get("review_status") != "confirmed"
                or alert.get("handling_status") != "unacknowledged"
                or alert.get("notification_status") in {"skipped_history", "cancelled"}
                or alert.get("contact_stage", "primary") not in {"primary", "backup"}):
            raise PushError("ALERT_NOT_ELIGIBLE")
        try:
            # This is below A's 10-second plugin deadline. Cancellation propagates.
            async with asyncio.timeout(self.attempt_timeout), self.lock:
                stage = alert.get("contact_stage", "primary")
                # Old standalone recipient adapters have a no-argument snapshot.
                # Unified dispatch always supplies a stage to the real adapter.
                if "contact_stage" in alert:
                    enabled, recipients = await asyncio.to_thread(self.recipients.snapshot, stage)
                else:
                    enabled, recipients = await asyncio.to_thread(self.recipients.snapshot)
                if not enabled:
                    raise PushError("NOTIFICATIONS_DISABLED")
                if not recipients:
                    raise PushError("NO_REGISTERED_RECIPIENTS")
                semaphore = asyncio.Semaphore(self.concurrency)

                async def send(recipient):
                    async with semaphore:
                        if await asyncio.to_thread(self.ledger.accepted, alert["id"], recipient.key, contact_stage=stage):
                            await asyncio.to_thread(self.ledger.record, alert["id"], recipient.key, contact_stage=stage)
                            return True
                        try:
                            async with asyncio.timeout(self.send_timeout):
                                await self.transport.send(recipient, alert)
                                # A cancellation after acceptance but before local commit can
                                # repeat a request. Tag + client ledger mitigate, not exactly-once.
                                await asyncio.to_thread(self.ledger.record, alert["id"], recipient.key, contact_stage=stage)
                            return True
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            return False

                results = await asyncio.gather(*(send(r) for r in recipients))
                if not all(results):
                    raise PushError("RECIPIENTS_NOT_ALL_ACCEPTED")
                return {"accepted": True, "is_mock": False, "recipient_count": len(recipients)}
        except asyncio.CancelledError:
            raise
        except PushError:
            raise
        except Exception:
            raise PushError("PUSH_ATTEMPT_FAILED") from None


async def publish_alert(alert: dict, *, idempotency_key: str) -> dict:
    """A's exact plugin signature. Opt in using APP_ALERT_PUSH_HANDLER.

    Open a short-lived repository/client per dispatch; no event-loop-bound
    global objects survive restarts or alternate test loops.
    """
    if os.getenv("APP_FCM_ENABLED", "false").lower() != "true":
        raise PushError("FCM_NOT_ENABLED")
    credentials_file = os.getenv("APP_FCM_CREDENTIALS", "")
    if not credentials_file:
        raise PushError("FCM_CREDENTIALS_UNAVAILABLE")
    origin = os.getenv("APP_FCM_ORIGIN", "").rstrip("/")
    try:
        parts = urlsplit(origin)
        port = parts.port  # Validate malformed/non-numeric/out-of-range ports.
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
                or parts.path or parts.query or parts.fragment or len(origin) > 200):
            raise ValueError()
        host = parts.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        origin = parts.scheme + "://" + host
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            origin += ":" + str(port)
    except ValueError:
        raise PushError("FCM_ORIGIN_REQUIRED") from None
    repository = None
    try:
        if str(SERVER / "elder-safety/backend") not in sys.path:
            sys.path.insert(0, str(SERVER / "elder-safety/backend"))
        from safety_backend.config import Settings
        from safety_backend.repository import Repository
        repository = await asyncio.to_thread(lambda: Repository(Settings.from_env()))
        ledger = await asyncio.to_thread(AcceptanceLedger, Path(os.getenv("DATA_DIR", str(SERVER / "data"))) / "alert-center/push-acceptance.sqlite3")
        async with httpx.AsyncClient(timeout=httpx.Timeout(3, connect=2), trust_env=False, follow_redirects=False) as client:
            credentials = ServiceAccountToken(credentials_file, client)
            publisher = AlertPublisher(SafetyRecipients(repository), FcmTransport(client, credentials, scope=origin + "|default"), ledger)
            return await publisher.publish(alert, idempotency_key=idempotency_key)
    except asyncio.CancelledError:
        raise
    except PushError:
        raise
    except Exception:
        raise PushError("PUSH_CONFIGURATION_FAILED") from None
    finally:
        if repository is not None:
            await asyncio.to_thread(repository.engine.dispose)

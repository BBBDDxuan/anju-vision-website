"""SQLAlchemy data access; workers/API do not access SQL directly."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4
from sqlalchemy import (create_engine, event, MetaData, Table, Column, String, Text, Integer, Float, JSON, select, update, delete, and_, or_)
from .schemas import Preferences


def now():
    return datetime.now(timezone.utc)

def stamp(value=None):
    return (value or now()).astimezone(timezone.utc).isoformat()

def uid(prefix):
    return prefix + "_" + uuid4().hex

class Conflict(Exception):
    pass

class Missing(Exception):
    pass

class Repository:
    def __init__(self, settings):
        self.s = settings
        self.lock = RLock()
        self.engine = create_engine(settings.database_url, **({"connect_args": {"check_same_thread": False, "timeout": 30}} if settings.database_url.startswith("sqlite") else {}))
        if settings.database_url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def sqlite_setup(connection, record):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")
        m = MetaData()
        self.devices = Table("devices", m, Column("device_id", String(100), primary_key=True), Column("name", String(100)), Column("last_seen_at", String(40)), Column("offline_reported", Integer, default=0))
        self.events = Table("events", m, Column("event_id", String(100), primary_key=True), Column("device_id", String(100), index=True), Column("event_type", String(30)), Column("track_id", String(100)), Column("created_at", String(40), index=True), Column("occurred_at", String(40)), Column("fingerprint", String(64)), Column("body", JSON))
        self.jobs = Table("jobs", m, Column("job_id", String(100), primary_key=True), Column("event_id", String(100), index=True), Column("kind", String(20)), Column("contact_id", String(20)), Column("status", String(20)), Column("attempts", Integer), Column("due_at", String(40), index=True), Column("lease_until", String(40)), Column("last_error", Text))
        self.evidence = Table("evidence", m, Column("evidence_id", String(100), primary_key=True), Column("device_id", String(100)), Column("path", Text), Column("media_type", String(50)), Column("expires_at", String(40)))
        self.tokens = Table("push_tokens", m, Column("token_id", String(100), primary_key=True), Column("token_hash", String(64), unique=True), Column("token", Text), Column("platform", String(20)), Column("contact_id", String(20)))
        self.audit = Table("audit", m, Column("audit_id", String(100), primary_key=True), Column("event_id", String(100)), Column("kind", String(40)), Column("created_at", String(40)), Column("body", JSON))
        self.preferences = Table("preferences", m, Column("key", String(30), primary_key=True), Column("body", JSON))
        m.create_all(self.engine)
        with self.engine.begin() as c:
            if c.execute(select(self.preferences).where(self.preferences.c.key == "family")).first() is None:
                p = Preferences().model_dump()
                p["escalation_seconds"] = settings.escalation_seconds
                c.execute(self.preferences.insert().values(key="family", body=p))

    def _audit(self, c, event_id, kind, **body):
        c.execute(self.audit.insert().values(audit_id=uid("log"), event_id=event_id, kind=kind, created_at=stamp(), body=body))

    def _job(self, c, event_id, kind, contact="primary", due=None):
        c.execute(self.jobs.insert().values(job_id=uid("job"), event_id=event_id, kind=kind, contact_id=contact, status="pending", attempts=0, due_at=stamp(due), lease_until=None, last_error=None))

    def register(self, device_id, name):
        with self.lock, self.engine.begin() as c:
            existing = c.execute(select(self.devices).where(self.devices.c.device_id == device_id)).first()
            values = dict(name=name, last_seen_at=stamp(), offline_reported=0)
            if existing:
                c.execute(update(self.devices).where(self.devices.c.device_id == device_id).values(**values))
            else:
                c.execute(self.devices.insert().values(device_id=device_id, **values))
        return self.device(device_id)

    def heartbeat(self, device_id):
        with self.lock, self.engine.begin() as c:
            result = c.execute(update(self.devices).where(self.devices.c.device_id == device_id).values(last_seen_at=stamp(), offline_reported=0))
            if not result.rowcount:
                raise Missing("Device is not registered")
        return self.device(device_id)

    def devices_list(self):
        with self.engine.connect() as c:
            rows = c.execute(select(self.devices)).mappings().all()
        return [{"device_id": r["device_id"], "name": r["name"], "last_seen_at": r["last_seen_at"], "online": now() - datetime.fromisoformat(r["last_seen_at"]) < timedelta(seconds=self.s.offline_seconds)} for r in rows]

    def device(self, device_id):
        for value in self.devices_list():
            if value["device_id"] == device_id:
                return value
        raise Missing("Device not found")

    def ingest(self, request, internal=False):
        payload = request.model_dump(mode="json")
        event_id = payload.pop("event_id") or uid("evt")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        with self.lock, self.engine.begin() as c:
            # The device row serializes same-camera ingest on PostgreSQL; this MVP runs one API process on SQLite.
            device = c.execute(select(self.devices).where(self.devices.c.device_id == request.device_id).with_for_update()).first()
            if not device:
                raise Missing("Device is not registered")
            old = c.execute(select(self.events).where(self.events.c.event_id == event_id)).mappings().first()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Conflict("event_id already belongs to a different payload")
                return dict(old["body"], deduplicated=True)
            for evidence_id in (request.evidence_id, request.clip_evidence_id):
                if evidence_id:
                    item = c.execute(select(self.evidence).where(self.evidence.c.evidence_id == evidence_id)).mappings().first()
                    if not item or item["device_id"] != request.device_id or item["expires_at"] <= stamp():
                        raise Conflict("Evidence unavailable or belongs to another device")
            cutoff = stamp(now() - timedelta(seconds=self.s.dedup_seconds))
            candidates = c.execute(select(self.events).where(and_(self.events.c.device_id == request.device_id, self.events.c.event_type == request.event_type, self.events.c.track_id == request.track_id, self.events.c.created_at >= cutoff)).order_by(self.events.c.created_at.desc())).mappings().first()
            if candidates and not internal:
                return dict(candidates["body"], deduplicated=True)
            created = stamp()
            body = dict(payload, event_id=event_id, created_at=created, review_status="pending", review_summary=None, notification_status="pending", acknowledgement_status="unacknowledged", state="detected", deduplicated=False)
            c.execute(self.events.insert().values(event_id=event_id, device_id=request.device_id, event_type=request.event_type, track_id=request.track_id, occurred_at=payload["occurred_at"], created_at=created, fingerprint=fingerprint, body=body))
            self._job(c, event_id, "notify")
            self._job(c, event_id, "review")
            prefs = c.execute(select(self.preferences.c.body).where(self.preferences.c.key == "family")).scalar_one()
            if request.severity == "critical":
                self._job(c, event_id, "escalate", "backup", now() + timedelta(seconds=prefs["escalation_seconds"]))
            self._audit(c, event_id, "detected", source="offline_monitor" if internal else "device")
            return body

    def get_event(self, event_id):
        with self.engine.connect() as c:
            item = c.execute(select(self.events.c.body).where(self.events.c.event_id == event_id)).scalar_one_or_none()
        if item is None:
            raise Missing("Event not found")
        return item

    def attach_evidence(self, event_id, device_id, evidence):
        with self.lock, self.engine.begin() as c:
            body = c.execute(select(self.events.c.body).where(self.events.c.event_id == event_id).with_for_update()).scalar_one_or_none()
            if body is None:
                raise Missing("Event not found")
            if body["device_id"] != device_id:
                raise PermissionError("Device identity mismatch")
            body = dict(body)
            changes = evidence.model_dump(exclude_none=True)
            changed = False
            for field, evidence_id in changes.items():
                if body.get(field) and body[field] != evidence_id:
                    raise Conflict("Existing evidence cannot be replaced")
                item = c.execute(select(self.evidence).where(self.evidence.c.evidence_id == evidence_id)).mappings().first()
                if not item or item["device_id"] != device_id or item["expires_at"] <= stamp():
                    raise Conflict("Evidence unavailable or belongs to another device")
                if body.get(field) != evidence_id:
                    changed = True
                body[field] = evidence_id
            if changed:
                c.execute(update(self.events).where(self.events.c.event_id == event_id).values(body=body))
                self._audit(c, event_id, "evidence_attached", fields=list(changes))
                # Late keyframes may enable a previously unavailable review, without re-alerting.
                reviews = c.execute(select(self.jobs).where(and_(self.jobs.c.event_id == event_id, self.jobs.c.kind == "review"))).mappings().all()
                if "evidence_id" in changes and not any(j["status"] in {"pending", "running"} for j in reviews) and body["review_status"] in {"failed", "unavailable"}:
                    self._job(c, event_id, "review")
                    body["review_status"] = "pending"
                    c.execute(update(self.events).where(self.events.c.event_id == event_id).values(body=body))
            return body

    def list_events(self, event_type=None, state=None, limit=100, offset=0, after=None, before=None):
        query = select(self.events)
        if event_type:
            query = query.where(self.events.c.event_type == event_type)
        if after:
            query = query.where(self.events.c.occurred_at >= stamp(after))
        if before:
            query = query.where(self.events.c.occurred_at <= stamp(before))
        # Filter the JSON state in Python for portable SQLite/PostgreSQL semantics, then paginate.
        with self.engine.connect() as c:
            rows = c.execute(query.order_by(self.events.c.created_at.desc())).mappings()
            result = [r["body"] for r in rows if not state or r["body"]["state"] == state]
        return result[offset:offset + limit]

    def change_event(self, event_id, action):
        with self.lock, self.engine.begin() as c:
            body = c.execute(select(self.events.c.body).where(self.events.c.event_id == event_id).with_for_update()).scalar_one_or_none()
            if body is None:
                raise Missing("Event not found")
            body = dict(body)
            body["acknowledgement_status"] = "acknowledged"
            if body["state"] != "resolved":
                body["state"] = "resolved" if action == "resolve" else "acknowledged"
            c.execute(update(self.events).where(self.events.c.event_id == event_id).values(body=body))
            c.execute(update(self.jobs).where(and_(self.jobs.c.event_id == event_id, self.jobs.c.kind.in_(["escalate", "notify"]), self.jobs.c.status == "pending")).values(status="cancelled"))
            self._audit(c, event_id, action)
            return body

    def patch_event(self, event_id, **changes):
        with self.lock, self.engine.begin() as c:
            body = c.execute(select(self.events.c.body).where(self.events.c.event_id == event_id).with_for_update()).scalar_one_or_none()
            if body is None:
                return
            body = dict(body)
            if changes.get("state") == "notified" and body["state"] in {"acknowledged", "resolved"}:
                changes.pop("state")
            body.update(changes)
            c.execute(update(self.events).where(self.events.c.event_id == event_id).values(body=body))

    def add_evidence(self, device_id, path, media_type):
        item = dict(evidence_id=uid("evd"), device_id=device_id, path=str(path), media_type=media_type, expires_at=stamp(now() + timedelta(seconds=self.s.evidence_ttl_seconds)))
        with self.engine.begin() as c:
            c.execute(self.evidence.insert().values(**item))
        return item

    def get_evidence(self, evidence_id):
        with self.engine.connect() as c:
            item = c.execute(select(self.evidence).where(self.evidence.c.evidence_id == evidence_id)).mappings().first()
        if item is None:
            raise Missing("Evidence not found")
        return dict(item)

    def delete_event(self, event_id):
        paths = []
        with self.lock, self.engine.begin() as c:
            body = c.execute(select(self.events.c.body).where(self.events.c.event_id == event_id)).scalar_one_or_none()
            if body is None:
                raise Missing("Event not found")
            c.execute(delete(self.jobs).where(self.jobs.c.event_id == event_id))
            c.execute(delete(self.audit).where(self.audit.c.event_id == event_id))
            c.execute(delete(self.events).where(self.events.c.event_id == event_id))
            others = c.execute(select(self.events.c.body)).scalars().all()
            for eid in {body.get("evidence_id"), body.get("clip_evidence_id")} - {None}:
                if not any(eid in {b.get("evidence_id"), b.get("clip_evidence_id")} for b in others):
                    item = c.execute(select(self.evidence).where(self.evidence.c.evidence_id == eid)).mappings().first()
                    if item:
                        paths.append(item["path"])
                        c.execute(delete(self.evidence).where(self.evidence.c.evidence_id == eid))
        for path in paths:
            self.remove_file(path)

    def remove_file(self, path):
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.s.data_dir / "evidence"):
            raise ValueError("Evidence path is outside managed storage")
        resolved.unlink(missing_ok=True)

    def expire_evidence(self):
        with self.lock, self.engine.begin() as c:
            rows = c.execute(select(self.evidence).where(self.evidence.c.expires_at <= stamp())).mappings().all()
            for row in rows:
                self.remove_file(row["path"])
            # Keep tombstones to return 410 for expired evidence IDs.

    def get_preferences(self):
        with self.engine.connect() as c:
            return c.execute(select(self.preferences.c.body).where(self.preferences.c.key == "family")).scalar_one()

    def save_preferences(self, preferences):
        body = preferences.model_dump()
        with self.engine.begin() as c:
            c.execute(update(self.preferences).where(self.preferences.c.key == "family").values(body=body))
        return body

    def register_token(self, token):
        hashed = hashlib.sha256(token.token.encode()).hexdigest()
        with self.lock, self.engine.begin() as c:
            old = c.execute(select(self.tokens.c.token_id).where(self.tokens.c.token_hash == hashed)).scalar_one_or_none()
            token_id = old or uid("push")
            values = token.model_dump()
            if old:
                c.execute(update(self.tokens).where(self.tokens.c.token_id == old).values(**values))
            else:
                c.execute(self.tokens.insert().values(token_id=token_id, token_hash=hashed, **values))
        return {"token_id": token_id}

    def delete_token(self, token_id):
        with self.engine.begin() as c:
            if not c.execute(delete(self.tokens).where(self.tokens.c.token_id == token_id)).rowcount:
                raise Missing("Push token not found")

    def contact_tokens(self, contact_id):
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(self.tokens).where(self.tokens.c.contact_id == contact_id)).mappings()]

    def claim(self, kinds):
        with self.lock, self.engine.begin() as c:
            ready = and_(self.jobs.c.kind.in_(kinds), self.jobs.c.due_at <= stamp(), or_(self.jobs.c.status == "pending", and_(self.jobs.c.status == "running", self.jobs.c.lease_until <= stamp())))
            row = c.execute(select(self.jobs).where(ready).order_by(self.jobs.c.due_at).limit(1).with_for_update(skip_locked=True)).mappings().first()
            if not row:
                return None
            row = dict(row)
            changed = c.execute(update(self.jobs).where(and_(self.jobs.c.job_id == row["job_id"], ready)).values(status="running", attempts=row["attempts"] + 1, lease_until=stamp(now() + timedelta(seconds=max(120, self.s.request_timeout_seconds * 4)))))
            if not changed.rowcount:
                return None
            row["attempts"] += 1
            return row

    def finish(self, job, error=None):
        failed = error is not None
        terminal = job["attempts"] >= self.s.max_attempts
        status = ("failed" if terminal else "pending") if failed else "done"
        with self.lock, self.engine.begin() as c:
            c.execute(update(self.jobs).where(self.jobs.c.job_id == job["job_id"]).values(status=status, last_error=error, lease_until=None, due_at=stamp(now() + timedelta(seconds=self.s.retry_seconds * 2 ** (job["attempts"] - 1)))))
            if c.execute(select(self.events.c.event_id).where(self.events.c.event_id == job["event_id"])).first():
                self._audit(c, job["event_id"], job["kind"] + ("_failed" if failed else "_done"), contact_id=job["contact_id"], attempt=job["attempts"], error=error)
        return status

    def offline_devices(self):
        from .schemas import EventIn
        cutoff = stamp(now() - timedelta(seconds=self.s.offline_seconds))
        with self.lock, self.engine.begin() as c:
            rows = c.execute(select(self.devices).where(and_(self.devices.c.last_seen_at < cutoff, self.devices.c.offline_reported == 0))).mappings().all()
        for row in rows:
            # Stable ID makes a crash between event insertion and flag update harmless.
            event_id = "offline_" + hashlib.sha256((row["device_id"] + row["last_seen_at"]).encode()).hexdigest()[:32]
            self.ingest(EventIn(event_id=event_id, device_id=row["device_id"], event_type="device_offline", severity="warning", confidence=1, occurred_at=datetime.fromisoformat(row["last_seen_at"]), details={"signal": "heartbeat_timeout", "not_model_score": True}), internal=True)
            with self.engine.begin() as c:
                c.execute(update(self.devices).where(and_(self.devices.c.device_id == row["device_id"], self.devices.c.last_seen_at == row["last_seen_at"])).values(offline_reported=1))

    def records(self, name):
        table = {"jobs": self.jobs, "audit": self.audit}[name]
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(table)).mappings()]

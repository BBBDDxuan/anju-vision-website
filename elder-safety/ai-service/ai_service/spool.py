from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time


class SpoolFull(RuntimeError):
    pass


class Spool:
    """Single-process durable ledger. Device secrets/RTSP URLs are never persisted."""

    def __init__(self, root: Path, device_id: str, backend: str, max_bytes: int):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.root / "spool.sqlite3"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, frozen_payload TEXT,
                keyframe TEXT, evidence_id TEXT, delivered INTEGER NOT NULL DEFAULT 0,
                clip_path TEXT, clip_evidence_id TEXT,
                clip_status TEXT NOT NULL DEFAULT 'collecting', clip_note TEXT);
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, kind TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
                done INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                UNIQUE(event_id, kind));
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(events)")}
        if "frozen_payload" not in columns:
            self.db.execute("ALTER TABLE events ADD COLUMN frozen_payload TEXT")
        if "backend_event_id" not in columns:
            self.db.execute("ALTER TABLE events ADD COLUMN backend_event_id TEXT")
        if "keyframe_status" not in columns:
            self.db.execute("ALTER TABLE events ADD COLUMN keyframe_status TEXT NOT NULL DEFAULT 'pending'")
        for key, value in (("device_id", device_id), ("backend", backend)):
            row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            if row and row[0] != value:
                self.db.close()
                raise ValueError("spool belongs to another device/backend; use another state directory")
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))
        self.db.commit()
        # Old clients only acknowledged matching local IDs, but late media was
        # uploaded without association. Recover those jobs without losing files.
        with self.db:
            for row in self.db.execute("SELECT * FROM events WHERE delivered=1 AND backend_event_id IS NULL").fetchall():
                self.db.execute("UPDATE events SET backend_event_id=? WHERE event_id=?", (row["event_id"], row["event_id"]))
                frozen = json.loads(row["frozen_payload"] or row["payload"])
                if row["evidence_id"] and frozen.get("evidence_id") == row["evidence_id"]:
                    self.db.execute("UPDATE events SET keyframe_status='linked' WHERE event_id=?", (row["event_id"],))
                elif row["keyframe"] or row["evidence_id"]:
                    self._queue_media(row["event_id"], "keyframe")
                if row["clip_path"] or row["clip_evidence_id"]:
                    self._queue_media(row["event_id"], "clip")
                    self.db.execute("UPDATE events SET clip_status='pending' WHERE event_id=?", (row["event_id"],))
        # A crash before clip encoding cannot recover future frames. Keep alarms.
        self.db.execute("UPDATE events SET clip_status='interrupted', clip_note='capture interrupted before encoding' WHERE clip_status='collecting'")
        self.db.commit()

    def usage(self):
        total = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                # The encoder atomically renames its temporary MP4 in parallel.
                continue
        return total

    def check_capacity(self, extra=0):
        if self.usage()+extra > self.max_bytes:
            raise SpoolFull("state disk budget exhausted; export/archive evidence or increase budget")

    def add_event(self, payload: dict, keyframe: Path | None):
        self.check_capacity(8192)
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(event_id,payload,keyframe) VALUES(?,?,?)",
                (payload["event_id"], json.dumps(payload, allow_nan=False), str(keyframe) if keyframe else None))
            self.db.execute("INSERT INTO jobs(event_id,kind) VALUES(?, 'alarm')", (payload["event_id"],))

    def attach_clip(self, event_id, path: Path, note: str):
        with self.lock, self.db:
            self.db.execute("UPDATE events SET clip_path=?,clip_status='pending',clip_note=? WHERE event_id=?",
                            (str(path), note, event_id))
            self.db.execute("INSERT OR IGNORE INTO jobs(event_id,kind) VALUES(?, 'clip')", (event_id,))

    def clip_failed(self, event_id, reason):
        with self.lock, self.db:
            self.db.execute("UPDATE events SET clip_status='unavailable',clip_note=? WHERE event_id=?",
                            (reason, event_id))

    def due(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            # Alarms precede clips; an unavailable alarm does not block other jobs.
            row = self.db.execute("""SELECT jobs.* FROM jobs JOIN events USING(event_id)
                WHERE jobs.done=0 AND jobs.next_at<=?
                  AND (kind='alarm' OR (delivered=1 AND backend_event_id IS NOT NULL))
                ORDER BY CASE kind WHEN 'alarm' THEN 0 ELSE 1 END, jobs.id LIMIT 1""", (now,)).fetchone()
            return dict(row) if row else None

    def event(self, event_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            return dict(row) if row else None

    def save_evidence(self, event_id, field, evidence_id):
        if field not in ("evidence_id", "clip_evidence_id"):
            raise ValueError("invalid evidence field")
        with self.lock, self.db:
            self.db.execute(f"UPDATE events SET {field}=? WHERE event_id=?", (evidence_id, event_id))

    def freeze_payload(self, event_id, payload):
        content = json.dumps(payload, allow_nan=False)
        with self.lock, self.db:
            self.db.execute("UPDATE events SET frozen_payload=COALESCE(frozen_payload,?) WHERE event_id=?",
                            (content, event_id))
            return json.loads(self.db.execute("SELECT frozen_payload FROM events WHERE event_id=?",
                                              (event_id,)).fetchone()[0])

    def _queue_media(self, event_id, kind):
        self.db.execute("""INSERT INTO jobs(event_id,kind) VALUES(?,?)
            ON CONFLICT(event_id,kind) DO UPDATE SET done=0, next_at=0, last_error=NULL""", (event_id, kind))

    def acknowledge_alarm(self, job, backend_event_id):
        # Canonical ID and acknowledgement commit together: restart cannot mark
        # an alarm delivered while forgetting the ID needed by late evidence.
        with self.lock, self.db:
            row = self.event(job["event_id"])
            self.db.execute("UPDATE events SET delivered=1,backend_event_id=? WHERE event_id=?",
                            (backend_event_id, row["event_id"]))
            self.db.execute("UPDATE jobs SET done=1,last_error=NULL WHERE id=?", (job["id"],))
            frozen = json.loads(row["frozen_payload"] or row["payload"])
            for kind, field, path_field, status_field in (
                ("keyframe", "evidence_id", "keyframe", "keyframe_status"),
                ("clip", "clip_evidence_id", "clip_path", "clip_status"),
            ):
                if (backend_event_id == row["event_id"] and row[field]
                        and frozen.get(field) == row[field]):
                    self.db.execute(f"UPDATE events SET {status_field}='linked' WHERE event_id=?", (row["event_id"],))
                    self.db.execute("UPDATE jobs SET done=1,last_error=NULL WHERE event_id=? AND kind=?", (row["event_id"], kind))
                elif row[path_field] or row[field]:
                    self._queue_media(row["event_id"], kind)

    def done(self, job):
        if job["kind"] == "alarm":
            raise ValueError("alarm completion requires a canonical backend event ID")
        status = "keyframe_status" if job["kind"] == "keyframe" else "clip_status"
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET done=1,last_error=NULL WHERE id=?", (job["id"],))
            self.db.execute(f"UPDATE events SET {status}='linked' WHERE event_id=?", (job["event_id"],))

    def block(self, job, reason):
        # Terminal/manual intervention, distinct from successful delivery. Keep
        # both paths and uploaded IDs; never replace a backend evidence slot.
        status = "keyframe_status" if job["kind"] == "keyframe" else "clip_status"
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET done=1,last_error=? WHERE id=?", (reason, job["id"]))
            self.db.execute(f"UPDATE events SET {status}='association_blocked' WHERE event_id=?", (job["event_id"],))

    def blocked_count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM jobs WHERE done=1 AND last_error IS NOT NULL").fetchone()[0]

    def retry(self, job, reason, delay, now=None):
        now = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET attempts=attempts+1,next_at=?,last_error=? WHERE id=?",
                            (now+delay, reason, job["id"]))

    def pending_count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM jobs WHERE done=0").fetchone()[0]

    def events(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM events ORDER BY rowid")]

    def close(self):
        with self.lock:
            self.db.close()

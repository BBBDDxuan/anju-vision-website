from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3

from .errors import AppError


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Database:
    def __init__(self, data_dir: Path, journal_mode: str = 'WAL'):
        if journal_mode not in {'WAL', 'DELETE'}:
            raise ValueError('Unsupported SQLite journal mode')
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "memory.sqlite3"
        with self.connect() as c:
            # WAL shared-memory indexes are unsuitable for the Studio's
            # network-mounted workspace. Cloud uses rollback journaling.
            c.execute('PRAGMA journal_mode='+journal_mode)
            c.executescript("""
            CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                name TEXT NOT NULL, duration_ms INTEGER NOT NULL, original_path TEXT NOT NULL,
                playback_path TEXT NOT NULL, metadata TEXT NOT NULL, active_run TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
                status TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories(run_id TEXT PRIMARY KEY REFERENCES jobs(id),
                video_id TEXT NOT NULL REFERENCES videos(id), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES memories(run_id),
                video_id TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_time ON events(video_id,start_ms,end_ms);
            CREATE TABLE IF NOT EXISTS charges(id INTEGER PRIMARY KEY, day TEXT NOT NULL,
                video_id TEXT NOT NULL, job_id TEXT NOT NULL, reserved REAL NOT NULL, actual REAL,
                payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, day TEXT NOT NULL,
                video_id TEXT NOT NULL, job_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS states(run_id TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(run_id,entity_id), FOREIGN KEY(run_id) REFERENCES memories(run_id));
            CREATE TABLE IF NOT EXISTS review_log(id INTEGER PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
                run_id TEXT, event_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS review_video ON review_log(video_id,id);
            CREATE TABLE IF NOT EXISTS safety_alerts(id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                run_id TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS safety_video ON safety_alerts(video_id,run_id);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def add_video(self, video):
        with self.connect() as c:
            c.execute("INSERT INTO videos VALUES(?,?,?,?,?,?,?,NULL)",
                      tuple(video[k] for k in ["id", "sha256", "name", "duration_ms", "original_path", "playback_path"]) + (encode(video["metadata"]),))

    def video(self, video_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if not row:
            raise AppError("NOT_FOUND", "未找到该视频。", 404)
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def list_videos(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT id,name,duration_ms,sha256,active_run FROM videos ORDER BY rowid DESC")]

    def save_job(self, job):
        with self.connect() as c:
            c.execute("INSERT INTO jobs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload",
                      (job["id"], job["video_id"], job["status"], encode(job)))

    def job(self, job_id):
        with self.connect() as c:
            row = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise AppError("NOT_FOUND", "未找到该分析任务。", 404)
        return json.loads(row[0])

    def latest_job(self, video_id):
        with self.connect() as c:
            row = c.execute("SELECT payload FROM jobs WHERE video_id=? ORDER BY rowid DESC LIMIT 1", (video_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def interrupt_running(self, mark_reserved_unknown=False):
        with self.connect() as c:
            rows = c.execute("SELECT payload FROM jobs WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            job = json.loads(row[0])
            job.update(status="interrupted", stage="服务重启，分析已中断，请重新分析", error={"code": "INTERRUPTED", "message": "服务重启，未完成的分析不会视为成功。", "retryable": True})
            self.save_job(job)
        if mark_reserved_unknown:
            # Do not refund a request or invent a successful model response
            # when a restart interrupted local accounting.
            with self.connect() as c:
                c.execute("UPDATE requests SET status='unknown',payload=? WHERE status='reserved'",
                    (encode({'error': {'code': 'INTERRUPTED_ACCOUNTING',
                        'message': '服务重启时本地结算未完成；请求继续计入额度，费用与结果以服务商记录为准。'}}),))

    def save_memory(self, run_id, document, job=None, absence_seconds=300):
        from .safety import derive_alerts
        alerts = derive_alerts(document, run_id, job or self.job(run_id), absence_seconds)
        with self.connect() as c:
            c.execute("INSERT INTO memories VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload", (run_id, document["video_id"], encode(document)))
            c.execute('DELETE FROM events WHERE run_id=?', (run_id,))
            c.execute('DELETE FROM states WHERE run_id=?', (run_id,))
            c.executemany("INSERT INTO events VALUES(?,?,?,?,?,?)", [(e["id"], run_id, e["video_id"], e["start_ms"], e["end_ms"], encode(e)) for e in document["events"]])
            c.execute("UPDATE videos SET active_run=? WHERE id=?", (run_id, document["video_id"]))
            self._refresh_states(c, run_id)
            self._save_alerts(c, run_id, document['video_id'], alerts)

    def _save_alerts(self, c, run_id, video_id, alerts):
        previous = [(json.loads(row['payload']), bool(row['acknowledged'])) for row in
                    c.execute('SELECT payload,acknowledged FROM safety_alerts WHERE run_id=?', (run_id,))]
        for alert in alerts:
            acknowledged = any(ack and old['subject_id'] == alert['subject_id']
                               and old['action'] == alert['action']
                               and set(old['event_ids']) & set(alert['event_ids']) for old, ack in previous)
            c.execute('INSERT INTO safety_alerts(id,video_id,run_id,payload,acknowledged) VALUES(?,?,?,?,?) '
                      'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, '
                      'acknowledged=MAX(safety_alerts.acknowledged,excluded.acknowledged)',
                      (alert['id'], video_id, run_id, encode(alert), int(acknowledged)))
        ids = {alert['id'] for alert in alerts}
        obsolete = [row[0] for row in c.execute('SELECT id FROM safety_alerts WHERE run_id=?', (run_id,)) if row[0] not in ids]
        c.executemany('DELETE FROM safety_alerts WHERE id=?', [(identifier,) for identifier in obsolete])

    def refresh_active_alerts(self, absence_seconds=300):
        """Rebuild derived alerts after rule updates; leave source memories intact."""
        from .safety import derive_alerts
        for video in self.list_videos():
            if not video['active_run']:
                continue
            document, job = self.memory(video['id'])
            alerts = derive_alerts(document, job['id'], job, absence_seconds)
            with self.connect() as c:
                self._save_alerts(c, job['id'], video['id'], alerts)

    def safety_alerts(self, video_id, *, run_id=None):
        from .safety import prioritize
        video = self.video(video_id)
        with self.connect() as c:
            rows = c.execute('SELECT payload,acknowledged FROM safety_alerts WHERE video_id=? AND run_id=?',
                             (video_id, run_id if run_id is not None else video['active_run'])).fetchall()
        return sorted([{**prioritize(json.loads(row['payload'])), 'acknowledged': bool(row['acknowledged'])} for row in rows],
                      key=lambda alert: (alert['acknowledged'], alert['severity'] != 'urgent', -alert['start_ms']))

    def acknowledge_alert(self, video_id, alert_id, *, run_id=None):
        video = self.video(video_id)
        with self.connect() as c:
            cursor = c.execute('UPDATE safety_alerts SET acknowledged=1 WHERE id=? AND video_id=? AND run_id=?',
                               (alert_id, video_id, run_id if run_id is not None else video['active_run']))
            if not cursor.rowcount:
                raise AppError('NOT_FOUND', '未找到当前录像分析中的提醒。', 404)
        return {'id': alert_id, 'acknowledged': True}

    def _refresh_states(self, c, run_id):
        from dataclasses import asdict
        from .review import effective_memory
        from .state import replay
        memory = c.execute('SELECT payload,video_id FROM memories WHERE run_id=?', (run_id,)).fetchone()
        if not memory:
            return
        job = json.loads(c.execute('SELECT payload FROM jobs WHERE id=?', (run_id,)).fetchone()[0])
        records = [{**dict(r), 'payload': json.loads(r['payload'])} for r in c.execute('SELECT * FROM review_log WHERE video_id=? ORDER BY id', (memory['video_id'],))]
        document = effective_memory(json.loads(memory['payload']), job, records)
        states = replay(document, document['video_duration_ms'])
        c.execute('DELETE FROM states WHERE run_id=?', (run_id,))
        c.executemany('INSERT INTO states VALUES(?,?,?)', [(run_id, identifier, encode(asdict(state))) for identifier, state in states.items()])

    def refresh_active_states(self):
        with self.connect() as c:
            runs = [row[0] for row in c.execute('SELECT active_run FROM videos WHERE active_run IS NOT NULL')]
            for run_id in runs:
                self._refresh_states(c, run_id)

    def raw_memory(self, video_id):
        video = self.video(video_id)
        if not video["active_run"]:
            raise AppError("NOT_ANALYZED", "视频尚未完成分析，请先触发分析。", 409)
        with self.connect() as c:
            row = c.execute("SELECT payload FROM memories WHERE run_id=?", (video["active_run"],)).fetchone()
        return json.loads(row[0]), self.job(video["active_run"])

    def memory(self, video_id):
        from .review import effective_memory
        document, job = self.raw_memory(video_id)
        return effective_memory(document, job, self.review_records(video_id)), job

    def review_records(self, video_id):
        with self.connect() as c:
            return [{**dict(row), 'payload': json.loads(row['payload'])} for row in
                    c.execute('SELECT * FROM review_log WHERE video_id=? ORDER BY id', (video_id,))]

    def append_review(self, video_id, run_id, event_id, kind, payload, expected_revision=None):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            video = c.execute('SELECT active_run FROM videos WHERE id=?', (video_id,)).fetchone()
            if not video:
                raise AppError('NOT_FOUND', '未找到该视频。', 404)
            revision = c.execute('SELECT COALESCE(MAX(id),0) FROM review_log WHERE video_id=?', (video_id,)).fetchone()[0]
            if expected_revision is not None and revision != expected_revision:
                raise AppError('REVIEW_STALE', '复核记录已改变，请刷新后再提交。', 409)
            if run_id is not None and video['active_run'] != run_id:
                raise AppError('REVIEW_STALE', '分析版本已改变，请刷新后复核当前版本。', 409)
            running = c.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)).fetchone()
            if running:
                raise AppError('REVIEW_BUSY', '请等待视频分析完成后再保存复核。', 409)
            created = datetime.now(timezone.utc).isoformat()
            identifier = c.execute('INSERT INTO review_log VALUES(NULL,?,?,?,?,?,?)',
                                   (video_id, run_id, event_id, kind, encode(payload), created)).lastrowid
            if video['active_run']:
                self._refresh_states(c, video['active_run'])
        return identifier

    def reserve(self, video_id, job_id, amount, per_video, per_day):
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            daily = c.execute("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM charges WHERE day=?", (day,)).fetchone()[0]
            total = c.execute("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM charges WHERE video_id=?", (video_id,)).fetchone()[0]
            if amount <= 0 or daily + amount > per_day or total + amount > per_video:
                raise AppError("COST_LIMIT", "已达到费用预留上限，停止调用模型；重试也会计入预算。", 429)
            return c.execute("INSERT INTO charges(day,video_id,job_id,reserved,payload) VALUES(?,?,?,?,?)", (day, video_id, job_id, amount, "{}")).lastrowid

    def settle(self, charge_id, actual, usage):
        with self.connect() as c:
            c.execute("UPDATE charges SET actual=?,payload=? WHERE id=?", (actual, encode(usage), charge_id))

    def reserve_request(self, video_id, job_id, max_daily, max_job):
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            daily = c.execute("SELECT COUNT(*) FROM requests WHERE day=?", (day,)).fetchone()[0]
            per_job = c.execute("SELECT COUNT(*) FROM requests WHERE job_id=?", (job_id,)).fetchone()[0]
            if daily >= max_daily or per_job >= max_job:
                raise AppError("REQUEST_LIMIT", "已达到实际模型请求数量上限（失败与重试也计数）。", 429)
            return c.execute("INSERT INTO requests VALUES(NULL,?,?,?,?,?)", (day, video_id, job_id, "reserved", "{}")).lastrowid

    def finish_request(self, request_id, status, metadata):
        with self.connect() as c:
            c.execute("UPDATE requests SET status=?,payload=? WHERE id=?", (status, encode(metadata), request_id))

    def usage_summary(self):
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        with self.connect() as c:
            rows = c.execute("SELECT status,payload FROM requests WHERE day=?", (day,)).fetchall()
        payloads = [json.loads(row["payload"]) for row in rows]
        return {"day": day, "requests": len(rows), "input_tokens": sum(p.get("usage", {}).get("prompt_tokens", 0) or 0 for p in payloads),
                "output_tokens": sum(p.get("usage", {}).get("completion_tokens", 0) or 0 for p in payloads),
                "failed": sum(row["status"] == "failed" for row in rows),
                "unknown": sum(row["status"] == "unknown" for row in rows),
                "pending": sum(row["status"] == "reserved" for row in rows)}

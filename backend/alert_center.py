"""Persistent, single-household alert projection; source databases remain authoritative."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from .errors import AppError


def stamp():
    return datetime.now(timezone.utc).isoformat()


def alert_id(source, key):
    return 'a_' + hashlib.sha256(f'{source}:{key}'.encode()).hexdigest()[:40]


class AlertCenter:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'alerts.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, source_key TEXT NOT NULL,
                    body TEXT NOT NULL, UNIQUE(source,source_key));
                CREATE TABLE IF NOT EXISTS changes (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, alert_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (
                    alert_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0,
                    due REAL NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending');
            ''')

    @contextmanager
    def connect(self):
        with self.lock:
            c = sqlite3.connect(self.path, timeout=15)
            c.row_factory = sqlite3.Row
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise
            finally:
                c.close()

    def initialized(self, source):
        with self.connect() as c:
            return c.execute('SELECT 1 FROM metadata WHERE key=?', (source,)).fetchone() is not None

    def initialize(self, source):
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', (source, stamp()))

    @staticmethod
    def _save(c, body):
        body['updated_at'] = stamp()
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
        c.execute('INSERT INTO changes(alert_id,body) VALUES(?,?)', (body['id'], encoded))
        body['revision'] = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
        c.execute('UPDATE changes SET body=? WHERE seq=?', (encoded, body['revision']))
        c.execute('INSERT INTO alerts VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                  (body['id'], body['source'], body['source_key'], encoded))
        return body

    def upsert(self, projected, *, historical=False):
        projected = dict(projected)
        with self.connect() as c:
            row = c.execute('SELECT body FROM alerts WHERE id=?', (projected['id'],)).fetchone()
            old = json.loads(row['body']) if row else None
            body = dict(projected)
            body['handling_status'] = (old or {}).get('handling_status', 'unacknowledged')
            if projected.pop('source_acknowledged', False) and body['handling_status'] == 'unacknowledged':
                body['handling_status'] = 'acknowledged'
            if projected.pop('source_resolved', False):
                body['handling_status'] = 'resolved'
            body.pop('source_acknowledged', None)
            body.pop('source_resolved', None)
            body['created_at'] = (old or {}).get('created_at', stamp())
            body['notification_status'] = (old or {}).get('notification_status', 'skipped_history' if historical else 'pending')
            if body['handling_status'] != 'unacknowledged':
                self._cancel(c, body)
            if old and all(old.get(k) == v for k, v in body.items()):
                return old
            saved = self._save(c, body)
            if not old and not historical and body['handling_status'] == 'unacknowledged':
                c.execute('INSERT OR IGNORE INTO outbox(alert_id) VALUES(?)', (body['id'],))
            return saved

    def get(self, identifier):
        with self.connect() as c:
            row = c.execute('SELECT body FROM alerts WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise AppError('ALERT_NOT_FOUND', '未找到该告警。', 404)
        return json.loads(row['body'])

    def listing(self, limit=100, offset=0, source=None):
        with self.connect() as c:
            cursor = c.execute('SELECT COALESCE(MAX(seq),0) FROM changes').fetchone()[0]
            where, params = ('WHERE source=?', [source]) if source else ('', [])
            total = c.execute(f'SELECT COUNT(*) FROM alerts {where}', params).fetchone()[0]
            rows = c.execute(f'SELECT body FROM alerts {where} ORDER BY rowid DESC LIMIT ? OFFSET ?',
                             [*params, limit, offset]).fetchall()
        return {'alerts': [json.loads(r['body']) for r in rows], 'total': total,
                'offset': offset, 'has_more': offset + len(rows) < total, 'snapshot_cursor': cursor}

    def changes(self, after=0, limit=100):
        with self.connect() as c:
            rows = c.execute('SELECT seq,body FROM changes WHERE seq>? ORDER BY seq LIMIT ?',
                             (after, limit + 1)).fetchall()
            latest = c.execute('SELECT COALESCE(MAX(seq),0) FROM changes').fetchone()[0]
        selected = rows[:limit]
        return {'alerts': [json.loads(r['body']) for r in selected],
                'next_cursor': selected[-1]['seq'] if selected else after,
                'server_cursor': latest, 'has_more': len(rows) > limit}

    def reconcile(self, source, current_ids):
        """Keep history, but retract formal status when its original proof disappears."""
        with self.connect() as c:
            rows = c.execute('SELECT id,body FROM alerts WHERE source=?', (source,)).fetchall()
            for row in rows:
                body = json.loads(row['body'])
                if row['id'] not in current_ids and body['review_status'] == 'confirmed':
                    body['review_status'] = 'withdrawn'
                    self._cancel(c, body)
                    self._save(c, body)

    @staticmethod
    def _cancel(c, body):
        c.execute("UPDATE outbox SET state='cancelled' WHERE alert_id=? AND state IN ('pending','sending')", (body['id'],))
        if body['notification_status'] in {'pending', 'retrying'}:
            body['notification_status'] = 'cancelled'

    def cancel_notification(self, identifier):
        """Cancel delivery without changing review or human handling status."""
        with self.connect() as c:
            row = c.execute('SELECT body FROM alerts WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise AppError('ALERT_NOT_FOUND', '未找到该告警。', 404)
            body = json.loads(row['body'])
            previous = body['notification_status']
            self._cancel(c, body)
            return self._save(c, body) if previous != body['notification_status'] else body

    def handle(self, identifier, action):
        with self.connect() as c:
            row = c.execute('SELECT body FROM alerts WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise AppError('ALERT_NOT_FOUND', '未找到该告警。', 404)
            body = json.loads(row['body'])
            target = 'resolved' if action == 'resolve' else 'acknowledged'
            previous = body['notification_status']
            self._cancel(c, body)
            if body['handling_status'] == 'resolved' or body['handling_status'] == target:
                return self._save(c, body) if previous != body['notification_status'] else body
            body['handling_status'] = target
            return self._save(c, body)

    def save_image(self, identifier, number, path, allowed_root):
        """Own the evidence snapshot so a new source run cannot silently change it."""
        path, allowed_root = Path(path).resolve(), Path(allowed_root).resolve()
        if not path.is_relative_to(allowed_root) or not path.is_file() or path.stat().st_size > 10_000_000:
            return None
        content = path.read_bytes()
        if not content.startswith(b'\xff\xd8'):
            return None
        directory = self.root / 'evidence' / identifier
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f'{number}.jpg'
        if not target.exists():
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(content)
            temporary.replace(target)
        return {'url': f'/api/alerts/{identifier}/evidence/{number}', 'media_type': 'image/jpeg'}

    def evidence_path(self, identifier, number):
        item = self.get(identifier)
        if number >= len(item['evidence']):
            raise AppError('EVIDENCE_NOT_FOUND', '未找到该告警证据。', 404)
        return self.root / 'evidence' / identifier / f'{number}.jpg'

    def claim(self):
        with self.connect() as c:
            row = c.execute("SELECT * FROM outbox WHERE (state='pending' OR state='sending') AND due<=? ORDER BY due LIMIT 1",
                            (time.time(),)).fetchone()
            if not row:
                return None
            c.execute("UPDATE outbox SET state='sending',due=? WHERE alert_id=?", (time.time() + 30, row['alert_id']))
            return dict(row)

    def delivered(self, identifier, *, failed=False):
        with self.connect() as c:
            row = c.execute('SELECT body FROM alerts WHERE id=?', (identifier,)).fetchone()
            body = json.loads(row['body'])
            if body['handling_status'] != 'unacknowledged' or body['review_status'] != 'confirmed':
                previous = body['notification_status']
                self._cancel(c, body)
                return self._save(c, body) if previous != body['notification_status'] else body
            if failed:
                attempts = c.execute('SELECT attempts FROM outbox WHERE alert_id=?', (identifier,)).fetchone()[0] + 1
                c.execute("UPDATE outbox SET state='pending',attempts=?,due=? WHERE alert_id=?",
                          (attempts, time.time() + min(300, 2 ** min(attempts, 8)), identifier))
                body['notification_status'] = 'retrying'
            else:
                c.execute("UPDATE outbox SET state='sent' WHERE alert_id=?", (identifier,))
                body['notification_status'] = 'accepted_by_provider'
            return self._save(c, body)

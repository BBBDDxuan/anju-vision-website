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
                CREATE TABLE IF NOT EXISTS contact_escalations (
                    alert_id TEXT PRIMARY KEY, first_due REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending');
            ''')
            # Additive migration: old rows have no creation anchor and never
            # acquire a backup reminder merely because this version starts.
            c.execute('BEGIN IMMEDIATE')
            columns = {r['name'] for r in c.execute('PRAGMA table_info(outbox)')}
            if 'scheduled_at' not in columns:
                c.execute('ALTER TABLE outbox ADD COLUMN scheduled_at REAL')
            if 'escalation_planned' not in columns:
                c.execute('ALTER TABLE outbox ADD COLUMN escalation_planned INTEGER NOT NULL DEFAULT 0')

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
            c.execute('BEGIN IMMEDIATE')
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
            body.pop('contact_stage', None)  # A dispatch field, never source authority.
            body.pop('contact_escalation', None)
            if old and 'contact_escalation' in old:
                body['contact_escalation'] = dict(old['contact_escalation'])
            if body['handling_status'] != 'unacknowledged' or body['review_status'] != 'confirmed':
                self._cancel(c, body)
            elif not self._important(body):
                count = c.execute("UPDATE contact_escalations SET state='cancelled' "
                                  "WHERE alert_id=? AND state IN ('pending','sending')", (body['id'],)).rowcount
                if count and 'contact_escalation' in body:
                    body['contact_escalation']['backup_status'] = 'cancelled'
            if old and all(old.get(k) == v for k, v in body.items()):
                return old
            if (not old and not historical and body['handling_status'] == 'unacknowledged'
                    and body['review_status'] == 'confirmed'):
                scheduled = time.time()
                body['contact_escalation'] = {
                    'primary_scheduled_at': datetime.fromtimestamp(scheduled, timezone.utc).isoformat(),
                    'primary_status': 'pending', 'backup_due_at': None, 'backup_status': 'not_scheduled'}
                c.execute('INSERT OR IGNORE INTO outbox(alert_id,scheduled_at) VALUES(?,?)', (body['id'], scheduled))
            return self._save(c, body)

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
            c.execute('BEGIN IMMEDIATE')
            rows = c.execute('SELECT id,body FROM alerts WHERE source=?', (source,)).fetchall()
            for row in rows:
                body = json.loads(row['body'])
                if row['id'] not in current_ids and body['review_status'] == 'confirmed':
                    body['review_status'] = 'withdrawn'
                    self._cancel(c, body)
                    self._save(c, body)

    @staticmethod
    def _cancel(c, body):
        changed = False
        for table, stage in [('outbox', 'primary'), ('contact_escalations', 'backup')]:
            count = c.execute(f"UPDATE {table} SET state='cancelled' WHERE alert_id=? AND state IN ('pending','sending')",
                              (body['id'],)).rowcount
            if count and 'contact_escalation' in body:
                body['contact_escalation'][stage + '_status'] = 'cancelled'
            changed = bool(count) or changed
        if body['notification_status'] in {'pending', 'retrying'}:
            body['notification_status'] = 'cancelled'
            changed = True
        return changed

    def cancel_notification(self, identifier):
        """Cancel delivery without changing review or human handling status."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM alerts WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise AppError('ALERT_NOT_FOUND', '未找到该告警。', 404)
            body = json.loads(row['body'])
            return self._save(c, body) if self._cancel(c, body) else body

    def handle(self, identifier, action):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM alerts WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise AppError('ALERT_NOT_FOUND', '未找到该告警。', 404)
            body = json.loads(row['body'])
            target = 'resolved' if action == 'resolve' else 'acknowledged'
            cancelled = self._cancel(c, body)
            if body['handling_status'] == 'resolved' or body['handling_status'] == target:
                return self._save(c, body) if cancelled else body
            body['handling_status'] = target
            return self._save(c, body)

    def save_image(self, identifier, number, path, allowed_root, *, versioned=False):
        """Own the evidence snapshot so a new source run cannot silently change it."""
        path, allowed_root = Path(path).resolve(), Path(allowed_root).resolve()
        if not path.is_relative_to(allowed_root) or not path.is_file() or path.stat().st_size > 10_000_000:
            return None
        content = path.read_bytes()
        if not content.startswith(b'\xff\xd8'):
            return None
        directory = self.root / 'evidence' / identifier
        directory.mkdir(parents=True, exist_ok=True)
        version = hashlib.sha256(content).hexdigest() if versioned else None
        filename = f'{number}-{version}.jpg' if version else f'{number}.jpg'
        target = directory / filename
        if not target.exists():
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(content)
            temporary.replace(target)
        url = f'/api/alerts/{identifier}/evidence/{number}'
        return {'url': url + ('?version=' + version if version else ''), 'media_type': 'image/jpeg',
                **({'version': version} if version else {})}

    def evidence_path(self, identifier, number, version=None):
        item = self.get(identifier)
        if number < 0 or number >= len(item['evidence']):
            raise AppError('EVIDENCE_NOT_FOUND', '未找到该告警证据。', 404)
        record = item['evidence'][number]
        if version is not None and (len(version) != 64 or any(c not in '0123456789abcdef' for c in version)
                                    or record.get('version') != version):
            raise AppError('EVIDENCE_NOT_FOUND', '未找到该告警证据。', 404)
        directory = self.root / 'evidence' / identifier
        # Old URLs still resolve their frozen bytes; records created after this
        # change also support callers that omit the optional version parameter.
        if version is None and not (directory / f'{number}.jpg').is_file():
            version = record.get('version')
        filename = f'{number}-{version}.jpg' if version else f'{number}.jpg'
        return directory / filename

    @staticmethod
    def _important(body):
        return (body.get('source') in {'live_camera', 'uploaded_recording'}
                and body.get('severity') in {'critical', 'high', 'urgent'}
                and body.get('event_type') in {'suspected_fall', 'fire_detected', 'smoke_detected', 'help_requested'})

    @staticmethod
    def _plan_escalations(c, preferences):
        seconds = preferences.get('escalation_seconds', 120)
        if type(seconds) is not int or not 5 <= seconds <= 86400:
            raise ValueError('Invalid contact escalation interval')
        roles = {p.get('contact_id') for p in preferences.get('contacts', [])}
        rows = c.execute('''SELECT o.alert_id,o.scheduled_at,a.body FROM outbox o
                            JOIN alerts a ON a.id=o.alert_id
                            WHERE o.scheduled_at IS NOT NULL AND o.escalation_planned=0
                            AND o.state IN ('pending','sending','sent')''').fetchall()
        for row in rows:
            body = json.loads(row['body'])
            eligible = (body['handling_status'] == 'unacknowledged' and body['review_status'] == 'confirmed'
                        and AlertCenter._important(body)
                        and body['notification_status'] not in {'skipped_history', 'cancelled'}
                        and 'backup' in roles)
            c.execute('UPDATE outbox SET escalation_planned=1 WHERE alert_id=?', (row['alert_id'],))
            stages = body['contact_escalation']
            if eligible:
                due = row['scheduled_at'] + seconds
                c.execute('INSERT OR IGNORE INTO contact_escalations(alert_id,first_due,due) VALUES(?,?,?)',
                          (row['alert_id'], due, due))
                stages['backup_due_at'] = datetime.fromtimestamp(due, timezone.utc).isoformat()
                stages['backup_status'] = 'pending'
            else:
                stages['backup_status'] = 'not_required'
            AlertCenter._save(c, body)

    def claim(self, *, preferences=None):
        with self.connect() as c:
            # Serialize claims across repository instances as well as threads.
            c.execute('BEGIN IMMEDIATE')
            if preferences is not None:
                if preferences.get('notifications_enabled') is not True:
                    return None
                self._plan_escalations(c, preferences)
            now = time.time()
            row = c.execute('''SELECT alert_id,attempts,due,state,'primary' AS contact_stage FROM outbox
                               WHERE state IN ('pending','sending') AND due<=?
                               UNION ALL
                               SELECT alert_id,attempts,due,state,'backup' AS contact_stage FROM contact_escalations
                               WHERE state IN ('pending','sending') AND due<=?
                               ORDER BY due,contact_stage DESC LIMIT 1''', (now, now)).fetchone()
            if not row:
                return None
            table = 'outbox' if row['contact_stage'] == 'primary' else 'contact_escalations'
            c.execute(f"UPDATE {table} SET state='sending',due=? WHERE alert_id=?", (now + 30, row['alert_id']))
            return dict(row)

    def delivered(self, identifier, *, failed=False, contact_stage='primary'):
        if contact_stage not in {'primary', 'backup'}:
            raise ValueError('Unknown contact stage')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM alerts WHERE id=?', (identifier,)).fetchone()
            body = json.loads(row['body'])
            if body['handling_status'] != 'unacknowledged' or body['review_status'] != 'confirmed':
                return self._save(c, body) if self._cancel(c, body) else body
            table = 'outbox' if contact_stage == 'primary' else 'contact_escalations'
            job = c.execute(f'SELECT state,attempts FROM {table} WHERE alert_id=?', (identifier,)).fetchone()
            # A late completion must not revive cancellation or already-sent work.
            if not job or job['state'] not in {'pending', 'sending'}:
                return body
            if failed:
                attempts = job['attempts'] + 1
                c.execute(f"UPDATE {table} SET state='pending',attempts=?,due=? WHERE alert_id=?",
                          (attempts, time.time() + min(300, 2 ** min(attempts, 8)), identifier))
                status = 'retrying'
            else:
                c.execute(f"UPDATE {table} SET state='sent' WHERE alert_id=?", (identifier,))
                status = 'accepted_by_provider'
            if 'contact_escalation' in body:
                body['contact_escalation'][contact_stage + '_status'] = status
            accepted = c.execute("SELECT 1 FROM outbox WHERE alert_id=? AND state='sent' UNION ALL "
                                 "SELECT 1 FROM contact_escalations WHERE alert_id=? AND state='sent'",
                                 (identifier, identifier)).fetchone()
            body['notification_status'] = 'accepted_by_provider' if accepted else status
            return self._save(c, body)

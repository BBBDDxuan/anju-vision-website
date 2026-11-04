"""Adapt confirmed source records without running cameras or inference."""
from datetime import datetime, timezone
from urllib.parse import quote

from .alert_center import alert_id
from .errors import AppError


TYPES = {'suspected_fall': '疑似跌倒', 'fire_detected': '可见明火', 'help_requested': '疑似求助'}
ACTIONS = {'suspected_fall': 'fall', 'fire_detected': 'flame', 'help_requested': 'help_gesture'}


def confirmed(value):
    return (value.get('verdict') == 'confirmed' and value.get('cloud_images') is True
            and isinstance(value.get('model'), str) and bool(value['model'].strip()))


def utc(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed.utcoffset() is not None else None


class AlertSources:
    def __init__(self, center, db, safety):
        self.center, self.db, self.safety = center, db, safety
        self.status = {}

    def sync(self):
        for source, operation in [('live_camera', self.live), ('uploaded_recording', self.videos)]:
            try:
                operation(not self.center.initialized(source))
                self.center.initialize(source)
                self.status[source] = {'ready': True, 'last_sync_at': datetime.now(timezone.utc).isoformat()}
            except Exception:
                # Never include provider exceptions, paths or credential-bearing URLs.
                self.status[source] = {'ready': False, 'error': 'SOURCE_SYNC_FAILED'}

    def evidence(self, identifier):
        try:
            return self.center.get(identifier)['evidence']
        except AppError:
            return []

    def live(self, historical):
        offset = 0
        current_ids = set()
        while True:
            batch = self.safety.list_events(limit=200, offset=offset)
            for item in batch:
                details = item.get('details') or {}
                kind = item['event_type']
                proof = {'verdict': details.get('review_status'), 'cloud_images': details.get('cloud_images'),
                         'model': details.get('review_model')}
                if (kind not in TYPES or details.get('source_kind') != 'web_camera_preview'
                        or details.get('simulated') is not False or not confirmed(proof)):
                    continue
                key = item['event_id']
                identifier = alert_id('live_camera', key)
                current_ids.add(identifier)
                evidence = self.evidence(identifier)
                if not evidence and item.get('evidence_id'):
                    try:
                        record = self.safety.get_evidence(item['evidence_id'])
                        if utc(record['expires_at']) > datetime.now(timezone.utc).isoformat():
                            saved = self.center.save_image(identifier, 0, record['path'], self.safety.s.data_dir / 'evidence')
                            if saved:
                                evidence.append(saved)
                    except Exception:
                        pass  # A temporarily missing frame must not drop the confirmed event.
                saved_item = self.center.upsert({
                    'id': identifier, 'source': 'live_camera', 'source_key': key,
                    'source_event_id': key, 'camera_id': item['device_id'], 'video_id': None,
                    'event_type': kind, 'title': TYPES[kind], 'severity': item['severity'],
                    'occurred_at': utc(item['occurred_at']), 'video_offset_ms': None,
                    'review_status': 'confirmed', 'review_model': proof['model'],
                    'review_reason': str(details.get('review_reason', ''))[:800],
                    'evidence': evidence, 'evidence_status': 'available' if evidence else 'pending_or_unavailable',
                    'playback_url': None,
                    'source_acknowledged': item['acknowledgement_status'] == 'acknowledged',
                    'source_resolved': item['state'] == 'resolved',
                }, historical=historical)
                if saved_item['handling_status'] != 'unacknowledged' and item['state'] != 'resolved':
                    if item['acknowledgement_status'] != 'acknowledged' or saved_item['handling_status'] == 'resolved':
                        self.acknowledge_source(saved_item, 'resolve' if saved_item['handling_status'] == 'resolved' else 'ack')
            if len(batch) < 200:
                break
            offset += len(batch)
        self.center.reconcile('live_camera', current_ids)

    def videos(self, historical):
        current_ids = set()
        for video in self.db.list_videos():
            if not video['active_run']:
                continue
            try:
                document, job = self.db.memory(video['id'])
            except AppError as exc:
                if exc.code in {'NOT_FOUND', 'NOT_ANALYZED'}:
                    continue
                raise
            if job.get('is_mock') or document.get('fixture_kind') != 'model_analysis':
                continue
            # list_videos and memory are separate reads; a new run may start
            # between them. Bind proof, source alerts, identity and frames to
            # the actual memory's job rather than the earlier list snapshot.
            run_id = job['id']
            proofs = {}
            for candidate_id, result in job.get('safety', {}).get('reviews', {}).items():
                if confirmed(result) and result.get('candidate_id') == candidate_id:
                    for event_id in result.get('event_ids', []):
                        proofs[event_id] = result
            events = {e['id']: e for e in document['events']}
            evidence_refs = {e['id']: e for e in document['evidence']}
            frames = {f['id']: f for f in document['frames']}
            for item in self.db.safety_alerts(video['id'], run_id=run_id):
                ids = item['event_ids']
                matching = [proofs.get(eid) for eid in ids]
                if (not ids or not all(matching) or item.get('is_mock')
                        or any(eid not in events or events[eid]['action'] != item['action'] for eid in ids)):
                    continue
                kind = matching[0].get('event_type')
                if kind not in TYPES or ACTIONS[kind] != item['action'] or any(r.get('event_type') != kind for r in matching):
                    continue
                key = f"{video['id']}:{run_id}:{item['id']}"
                identifier = alert_id('uploaded_recording', key)
                current_ids.add(identifier)
                # Once available, snapshot bytes and their offsets stay paired.
                evidence = self.evidence(identifier)
                seen = set()
                for ref in ([] if evidence else item['evidence_ids']):
                    for fid in evidence_refs.get(ref, {}).get('frame_ids', []):
                        if fid in seen or len(evidence) >= 3 or fid not in frames:
                            continue
                        seen.add(fid)
                        path = self.db.path.parent / 'runs' / run_id / 'frames' / f'{fid}.jpg'
                        saved = self.center.save_image(identifier, len(evidence), path, self.db.path.parent / 'runs')
                        if saved:
                            evidence.append({**saved, 'video_offset_ms': frames[fid]['pts_ms']})
                saved_item = self.center.upsert({
                    'id': identifier, 'source': 'uploaded_recording', 'source_key': key,
                    'source_event_id': item['id'], 'camera_id': None, 'video_id': video['id'],
                    'run_id': run_id, 'event_type': kind, 'title': TYPES[kind],
                    'severity': 'critical' if item['severity'] == 'urgent' else 'warning',
                    # Upload time is not the historical incident's wall-clock time.
                    'occurred_at': None, 'video_offset_ms': item['start_ms'], 'video_end_ms': item['end_ms'],
                    'review_status': 'confirmed', 'review_model': matching[0]['model'],
                    'review_reason': str(matching[0].get('reason', ''))[:800],
                    'evidence': evidence, 'evidence_status': 'available' if evidence else 'pending_or_unavailable',
                    'playback_url': f"/media/videos/{quote(video['id'], safe='')}",
                    'source_acknowledged': item['acknowledged'], 'source_resolved': False,
                }, historical=historical)
                if saved_item['handling_status'] != 'unacknowledged' and not item['acknowledged']:
                    self.acknowledge_source(saved_item, 'ack')
        self.center.reconcile('uploaded_recording', current_ids)

    def acknowledge_source(self, item, action):
        if item['source'] == 'live_camera':
            self.safety.change_event(item['source_event_id'], action)
        else:
            self.db.acknowledge_alert(item['video_id'], item['source_event_id'], run_id=item['run_id'])

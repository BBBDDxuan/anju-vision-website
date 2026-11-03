"""Evidence-linked historical-video attention signals, never medical diagnoses."""
import hashlib
import math
from .events import ENVIRONMENT_ACTIONS
from .grouping import build_event_groups

RULES = {'fall': '疑似跌倒', 'lying_on_floor': '地面躺卧需关注',
         'climb': '攀爬行为需关注', 'help_gesture': '疑似求助动作',
         'smoke': '疑似烟雾', 'flame': '可见明火线索', 'sparks': '疑似火花／电弧'}
PRIORITY_ACTIONS = {'fall', 'help_gesture', 'long_absence'} | ENVIRONMENT_ACTIONS


def prioritize(alert):
    return {**alert, 'severity': 'urgent' if alert['action'] in PRIORITY_ACTIONS else alert['severity']}


def derive_alerts(document, run_id, job=None, absence_seconds=300):
    entities = {e['id']: e for e in document['entities']}
    evidence = {e['id']: e for e in document['evidence']}
    frames = {f['id']: f for f in document['frames']}
    valid_events = {}
    for event in document['events']:
        subject = entities.get(event['subject_id'], {})
        environment = event['action'] in ENVIRONMENT_ACTIONS
        if (subject.get('entity_type') not in {'object', 'location'} if environment else subject.get('entity_type') != 'person'):
            continue
        score = event.get('model_score')
        refs = [evidence.get(ref) for ref in event['evidence_ids']]
        support = [frames.get(fid) for ref in refs if ref for fid in ref['frame_ids']]
        valid = (event['action'] in RULES and event['epistemic_type'] in {'observed', 'inferred'}
                 and refs and all(refs) and support and all(support)
                 and all(f.get('kind') == 'original_frame' and event['start_ms'] <= f['pts_ms'] <= event['end_ms'] for f in support)
                 and len({f['pts_ms'] for f in support}) >= 2
                 and (score is None or (math.isfinite(score) and .55 <= score <= 1)))
        if valid:
            valid_events[event['id']] = event
    # Use the same occurrence boundaries as the timeline and QA. Validate each
    # original source before grouping so weak evidence cannot create an alert.
    groups = [[valid_events[eid] for eid in group['event_ids'] if eid in valid_events
               and valid_events[eid]['action'] not in ENVIRONMENT_ACTIONS]
              for group in build_event_groups(document, job or {})]
    # Smoke may persist across sampling gaps. Flame/spark bursts remain separate
    # across gaps: an extinguished lighter can be lit again within two seconds.
    environmental = {}
    for event in sorted(document['events'], key=lambda e: (e['start_ms'], e['end_ms'], e['id'])):
        subject = event['subject_id']
        if event['action'] in {'unknown', 'occluded'} or event['epistemic_type'] == 'unknown':
            environmental = {k: v for k, v in environmental.items() if k[0] != subject}
        if event['action'] not in ENVIRONMENT_ACTIONS:
            continue
        key = tuple(event.get(k) for k in ('subject_id','action','target_id','epistemic_type','review_status'))
        if event['id'] not in valid_events:
            environmental.pop(key, None)
            continue
        previous = environmental.get(key)
        gap_ms = 2000 if event['action'] == 'smoke' else 0
        if previous and event['start_ms'] <= max(e['end_ms'] for e in previous)+gap_ms:
            previous.append(event)
        else:
            environmental[key] = [event]
            groups.append(environmental[key])
    alerts = []
    for members in groups:
        if not members:
            continue
        first = members[0]
        start, end = min(e['start_ms'] for e in members), max(e['end_ms'] for e in members)
        if first['action'] == 'lying_on_floor' and end-start < 10000:
            continue
        identifier = hashlib.sha256(f"{run_id}:{first['subject_id']}:{first['action']}:{first['id']}".encode()).hexdigest()[:24]
        alerts.append({'id': 'alert_'+identifier, 'video_id': document['video_id'], 'run_id': run_id,
                       'subject_id': first['subject_id'], 'person_label': entities[first['subject_id']]['label'],
                       'subject_label': entities[first['subject_id']]['label'],
                       'category': 'environment' if first['action'] in ENVIRONMENT_ACTIONS else 'person',
                       'action': first['action'], 'title': RULES[first['action']],
                       'severity': 'urgent' if first['action'] in PRIORITY_ACTIONS else 'attention',
                       'start_ms': start, 'end_ms': end, 'event_ids': [e['id'] for e in members],
                       'evidence_ids': list(dict.fromkeys(ref for e in members for ref in e['evidence_ids'])),
                       'epistemic_type': 'observed' if all(e['epistemic_type'] == 'observed' for e in members) else 'inferred',
                       'reasons': list(dict.fromkeys(reason for e in members for reason in e['uncertainty_reasons'])),
                       'message': ('历史录像中的环境危险候选，请回看证据；不等同于确认火灾，也不代表当前现场。'
                                   if first['action'] in ENVIRONMENT_ACTIONS else
                                   '历史录像中的行为线索，请回看证据确认。提醒不代表当前现场，也不构成健康诊断。'),
                       'source': 'uploaded_recording', 'is_mock': document['fixture_kind'] != 'model_analysis'})
    return alerts + absence_alerts(document, run_id, job or {}, absence_seconds)


def absence_alerts(document, run_id, job, threshold_seconds):
    """Require explicit negative observations and analyzed coverage; silence is not absence."""
    entities = {e['id']: e for e in document['entities']}
    evidence = {e['id']: e for e in document['evidence']}
    frames = {f['id']: f for f in document['frames']}
    people = [e for e in document['events'] if entities.get(e['subject_id'], {}).get('entity_type') == 'person']
    ranges = sorted(job.get('analyzed_ranges', []), key=lambda r: r['start_ms'])

    def covered(start, end):
        cursor = start
        for span in ranges:
            if span['end_ms'] <= cursor:
                continue
            if span['start_ms'] > cursor:
                return False
            cursor = max(cursor, span['end_ms'])
            if cursor >= end:
                return True
        return False

    def supports(event, minimum=2):
        refs = [evidence.get(ref) for ref in event['evidence_ids']]
        selected = [frames.get(fid) for ref in refs if ref for fid in ref['frame_ids']]
        return (refs and all(refs) and selected and all(selected)
                and all(f.get('kind') == 'original_frame' and event['start_ms'] <= f['pts_ms'] <= event['end_ms'] for f in selected)
                and len({f['pts_ms'] for f in selected}) >= minimum)

    visible = [e for e in people if e['epistemic_type'] == 'observed' and e['action'] not in {'unknown', 'occluded'} and supports(e, 1)]
    episodes, current = [], []
    for event in sorted((e for e in document['events'] if e['action'] == 'no_person_visible'), key=lambda e: (e['start_ms'], e['end_ms'])):
        score = event.get('model_score')
        valid = (event['epistemic_type'] == 'observed' and entities.get(event['subject_id'], {}).get('entity_type') == 'location'
                 and score is not None and math.isfinite(score) and .75 <= score <= 1
                 and not event['uncertainty_reasons'] and supports(event)
                 and covered(event['start_ms'], event['end_ms'])
                 and not any(p['start_ms'] <= event['end_ms'] and p['end_ms'] >= event['start_ms'] for p in people))
        if not valid:
            current = []
            continue
        if current and (event['subject_id'] != current[-1]['subject_id'] or event['start_ms'] > max(e['end_ms'] for e in current)+5000
                        or not covered(current[0]['start_ms'], event['end_ms'])
                        or any(p['end_ms'] >= current[-1]['end_ms'] and p['start_ms'] <= event['start_ms'] for p in people)):
            current = []
        if not current:
            if not any(p['end_ms'] <= event['start_ms'] for p in visible):
                continue
            current = []
            episodes.append(current)
        current.append(event)
    alerts = []
    for members in episodes:
        start, end = members[0]['start_ms'], max(e['end_ms'] for e in members)
        if end-start < threshold_seconds*1000:
            continue
        identifier = hashlib.sha256(f"{run_id}:absence:{members[0]['id']}".encode()).hexdigest()[:24]
        alerts.append({'id': 'alert_'+identifier, 'video_id': document['video_id'], 'run_id': run_id,
                       'subject_id': members[0]['subject_id'], 'person_label': '画面内人物',
                       'action': 'long_absence', 'title': '长时间未出现（采样画面内无人）', 'severity': 'urgent',
                       'start_ms': start, 'end_ms': end, 'event_ids': [e['id'] for e in members],
                       'evidence_ids': list(dict.fromkeys(ref for e in members for ref in e['evidence_ids'])),
                       'epistemic_type': 'inferred', 'reasons': ['仅为同一摄像头的采样画面，不证明人物离家或遇险。'],
                       'message': f'先前见到人物，之后已分析片段的无人采样证据跨度达到{threshold_seconds//60}分钟。仅针对历史录像，不代表人物不在家或发生危险。',
                       'source': 'uploaded_recording', 'is_mock': document['fixture_kind'] != 'model_analysis'})
    return alerts

"""Bounded real-model audit of the ten local simulated falls. No production edits.

Reuse uploaded hashes and completed jobs; retain raw responses, evidence and
intermediate reports. The existing daily request cap is the enforced budget.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

import cv2
import httpx
import numpy as np

from backend.config import ROOT, Settings
from backend.db import Database
from backend.events import validate_memory
from backend.providers.formatting import parse_extraction

BASE = 'http://127.0.0.1:8000'
OUT = ROOT/'data/validation/fall-suite-20261020'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def sources():
    folder = ROOT/'video/public_samples'
    first = folder/'CAUCAFall_Subject1_FallForward/CAUCAFall_S1_simulated_fall_forward.mp4'
    manifest = json.loads((folder/'CAUCAFall_validation_9/MANIFEST.json').read_text(encoding='utf8'))
    return [{'subject':1,'action':'Fall forward','path':first},
            *[{'subject':c['subject'],'action':c['action'],'path':folder/'CAUCAFall_validation_9'/c['filename']}
              for c in manifest['clips']]]


def contact_sheet(source,number):
    cap = cv2.VideoCapture(str(source))
    assert cap.isOpened()
    fps, count = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    indexes = list(range(0,count,max(1,round(fps/2))))
    cells = []
    for index in indexes:
        cap.set(cv2.CAP_PROP_POS_FRAMES,index)
        ok, frame = cap.read()
        assert ok
        frame = cv2.resize(frame,(288,192))
        cv2.putText(frame,f'S{number:02d} {index/fps:.2f}s',(7,19),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,255,255),1)
        cells.append(frame)
    cap.release()
    cells += [np.zeros_like(cells[0]) for _ in range((-len(cells)) % 5)]
    canvas = np.vstack([np.hstack(cells[i:i+5]) for i in range(0,len(cells),5)])
    ok, data = cv2.imencode('.jpg',canvas,[cv2.IMWRITE_JPEG_QUALITY,85])
    assert ok
    target = OUT/f'S{number:02d}-contact.jpg'
    target.write_bytes(data.tobytes())
    return str(target.relative_to(ROOT))


def collect(client,db,record):
    vid = record['video_id']
    detail = client.get(f'/api/videos/{vid}').json()
    job = detail['latest_job']
    record['job'] = job
    if not detail.get('active_run'):
        return
    document, active = db.raw_memory(vid)
    validate_memory(document)
    memory = client.get(f'/api/videos/{vid}/events?grouped=true').json()
    (OUT/f'S{record["subject"]:02d}-memory.json').write_text(json.dumps(memory,ensure_ascii=False,indent=2),encoding='utf8')
    by_id = {e['id']:e for e in memory['events']}
    grouped_ids = [eid for g in memory['event_groups'] for eid in g['event_ids']]
    assert len(grouped_ids) == len(set(grouped_ids)) == len(by_id)
    falls = [e for e in memory['events'] if e['action'] == 'fall']
    groups = [g for g in memory['event_groups'] if by_id[g['event_ids'][0]]['action'] == 'fall']
    alerts = client.get(f'/api/videos/{vid}/alerts').json()
    answer = client.post(f'/api/videos/{vid}/query',json={'question':'视频中什么时候有人跌倒？'}).json()
    refs = {e['id'] for e in document['evidence']}
    assert all(ref in refs for c in answer['observations']+answer['inferences'] for ref in c['evidence_ids'])
    (OUT/f'S{record["subject"]:02d}-query.json').write_text(json.dumps(answer,ensure_ascii=False,indent=2),encoding='utf8')
    (OUT/f'S{record["subject"]:02d}-alerts.json').write_text(json.dumps(alerts,ensure_ascii=False,indent=2),encoding='utf8')
    invalid = 0
    for response in (db.path.parent/'runs'/active['id']).glob('response-*.json'):
        body = json.loads(response.read_text(encoding='utf8'))
        try:
            parse_extraction(body['response']['choices'][0]['message']['content'])
        except (ValueError,KeyError,TypeError):
            invalid += 1
    record.update(raw_events=len(by_id),fall_source_records=len(falls),fall_display_groups=len(groups),
                  fall_groups=groups,fall_events=falls,
                  fall_alerts=[a for a in alerts['alerts'] if a['action'] == 'fall'],
                  other_priority_alerts=[a for a in alerts['alerts'] if a['action'] not in {'fall','lying_on_floor'}],
                  person_entities=[{'id':e['id'],'label':e['label']} for e in memory['entities'] if e.get('entity_type') == 'person'],
                  query_observations=len(answer['observations']),query_inferences=len(answer['inferences']),
                  schema_pass=True,all_sources_preserved=True,invalid_format_responses=invalid,
                  prompt=job.get('prompt_version'),retries=job.get('retries',0),
                  issues=([f'analysis_{job["status"]}'] if job['status'] != 'completed' else [])
                    + (['fall_not_extracted'] if not falls else [])
                    + (['possible_duplicate_fall_groups'] if len(groups)>1 else [])
                    + (['multiple_person_candidates'] if len([e for e in memory['entities'] if e.get('entity_type') == 'person'])>1 else [])
                    + (['no_fall_alert'] if falls and not any(a['action']=='fall' for a in alerts['alerts']) else []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-paid',action='store_true')
    parser.add_argument('--max-new-calls',type=int,default=17)
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    state_file = OUT/'suite.json'
    db = Database(Settings.from_env().data_dir)
    with httpx.Client(base_url=BASE,timeout=60) as client:
        health = client.get('/api/health').json()
        if state_file.exists():
            report = json.loads(state_file.read_text(encoding='utf8'))
        else:
            videos = client.get('/api/videos').json()['videos']
            report = {'created_at_utc':datetime.now(timezone.utc).isoformat(),'is_mock':False,
                'model':health['model'],'daily_limit':health['limits']['max_daily_calls'],
                'usage_before':health['usage'],'max_new_calls':args.max_new_calls,'clips':[],
                'existing_raw_hashes':{v['id']:digest(db.raw_memory(v['id'])[0]) for v in videos if v['active_run']},
                'limitations':['All clips are staged fall positives; no specificity or real elderly safety claims.',
                               'Visual contact-sheet checks are not dataset frame-level ground-truth annotations.']}
            for source in sources():
                path = source['path']
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                match = next((v for v in videos if v['sha256'] == sha),None)
                report['clips'].append({'subject':source['subject'],'action':source['action'],
                    'path':str(path.relative_to(ROOT)),'sha256':sha,'filename':path.name,
                    'video_id':match['id'] if match else None,'reused_existing_upload':bool(match),
                    'contact_sheet':contact_sheet(path,source['subject'])})

        def save():
            report['usage_after'] = client.get('/api/health').json()['usage']
            report['new_requests'] = report['usage_after']['requests']-report['usage_before']['requests']
            tmp = state_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            tmp.replace(state_file)

        save()
        if not args.run_paid:
            print(json.dumps({'prepared':len(report['clips']),'new_model_calls':0,'folder':str(OUT)}),flush=True)
            return
        assert health['analysis_ready'] and not health['is_mock'] and health['usage']['pending'] == 0
        # The already configured provider cap must enforce our entire budget.
        # Never alter .env or raise that cap for the suite.
        assert report['usage_before']['day'] == health['usage']['day']
        assert health['limits']['max_daily_calls']-report['usage_before']['requests'] <= args.max_new_calls
        queue, running = [], []
        for record in report['clips']:
            if not record['video_id']:
                with (ROOT/record['path']).open('rb') as stream:
                    response = client.post('/api/videos',files={'file':(record['filename'],stream,'video/mp4')})
                response.raise_for_status()
                record['video_id'] = response.json()['video_id']
                save()
            latest = client.get(f'/api/videos/{record["video_id"]}').json()['latest_job']
            if latest and latest['status'] == 'completed':
                record['reused_existing_analysis'] = True
                collect(client,db,record)
            elif latest and latest['status'] in {'queued','running'}:
                raise RuntimeError('An existing video is already running; do not overlap user-owned analysis.')
            else:
                queue.append(record)
        save()
        deadline = time.monotonic()+1200
        while queue or running:
            now = client.get('/api/health').json()
            if now['usage']['day'] != report['usage_before']['day']:
                for record in running:
                    client.post(f'/api/videos/{record["video_id"]}/pause')
                raise RuntimeError('Billing day changed; stopped new calls to retain the suite budget.')
            available = now['usage']['requests'] < now['limits']['max_daily_calls']
            while queue and len(running)<2 and available:
                record = queue.pop(0)
                latest = client.get(f'/api/videos/{record["video_id"]}').json()['latest_job']
                action = 'resume' if latest and latest.get('resumable') else 'analyze'
                response = client.post(f'/api/videos/{record["video_id"]}/{action}?auto_continue=false')
                if response.status_code == 429:
                    queue.insert(0,record)
                    break
                response.raise_for_status()
                record['started_job_id'] = response.json()['id']
                record['reused_existing_analysis'] = False
                running.append(record)
                print(json.dumps({'subject':record['subject'],'status':'started','action':action},ensure_ascii=False),flush=True)
                save()
            for record in running[:]:
                job = client.get(f'/api/jobs/{record["started_job_id"]}').json()
                if job['status'] in {'queued','running'}:
                    continue
                collect(client,db,record)
                running.remove(record)
                print(json.dumps({'subject':record['subject'],'status':job['status'],
                                  'fall_groups':record.get('fall_display_groups'),
                                  'calls':job['calls'],'issues':record.get('issues',[])},ensure_ascii=False),flush=True)
                save()
            if queue and not running and not available:
                report['budget_stopped_subjects'] = [r['subject'] for r in queue]
                break
            if time.monotonic()>deadline:
                raise RuntimeError('Suite monitoring deadline reached; do not restart in-flight calls.')
            if running:
                time.sleep(2)
        for record in report['clips']:
            assert hashlib.sha256((ROOT/record['path']).read_bytes()).hexdigest() == record['sha256']
        for vid,sha in report['existing_raw_hashes'].items():
            assert digest(db.raw_memory(vid)[0]) == sha, 'Previously completed analysis unexpectedly changed'
        report['original_videos_unchanged'] = report['existing_completed_analyses_unchanged'] = True
        report['completed_clips'] = sum(r.get('job',{}).get('status') == 'completed' for r in report['clips'])
        report['clips_with_extracted_falls'] = sum(bool(r.get('fall_source_records')) for r in report['clips'])
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        save()
        assert report['new_requests'] <= args.max_new_calls
        print(json.dumps({k:report.get(k) for k in ['completed_clips','clips_with_extracted_falls',
            'new_requests','budget_stopped_subjects','original_videos_unchanged']},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

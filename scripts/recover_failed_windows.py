"""Opt-in bounded recovery of this project's saved failed windows; preserves successful evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import httpx

from backend.config import ROOT, Settings
from backend.db import Database
from backend.events import validate_memory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-paid', action='store_true')
    parser.add_argument('--max-calls', type=int, choices=range(1,9), default=1)
    parser.add_argument('--report', choices=['first', 'remaining', 'final'], default='first')
    args = parser.parse_args()
    if not args.run_paid:
        parser.error('Recovery may incur API charges; explicit --run-paid required.')
    settings = Settings.from_env()
    backup = Path((settings.data_dir/'recovery-backup-path.txt').read_text(encoding='utf8'))
    manifest = json.loads((backup/'manifest.json').read_text(encoding='utf8'))
    vid, run_id = manifest['video_id'], manifest['job_id']
    base = 'http://127.0.0.1:8000'
    before = httpx.get(base+'/api/health').json()
    detail = httpx.get(f'{base}/api/videos/{vid}').json()
    assert detail['latest_job']['id'] == run_id, 'The user selected a different analysis version; aborted.'
    assert before['model'] == 'qwen3.8-max' and before['analysis_ready']
    if detail['latest_job']['status'] == 'completed':
        print('Already completed; no request made.')
        return
    result = httpx.post(f'{base}/api/videos/{vid}/resume?max_calls={args.max_calls}', timeout=15)
    result.raise_for_status()
    job = result.json()
    assert job['id'] == run_id
    last_stage = None
    deadline = time.monotonic()+args.max_calls*300+120
    while job['status'] in {'queued','running'}:
        if job['stage'] != last_stage:
            print(json.dumps({k:job.get(k) for k in ['status','stage','completed_windows','batch_calls']},ensure_ascii=False),flush=True)
            last_stage = job['stage']
        if time.monotonic() > deadline:
            raise RuntimeError('Monitoring deadline reached; do not automatically restart a running request.')
        time.sleep(1)
        job = httpx.get(f'{base}/api/jobs/{run_id}', timeout=10).json()
    db = Database(settings.data_dir)
    document, _ = db.raw_memory(vid)
    validate_memory(document)
    events = {e['id']:e for e in document['events']}
    old_events_preserved = all(events.get(e['id']) == e for e in manifest['original_events'])
    run = settings.data_dir/'runs'/run_id
    caches_preserved = all(hashlib.sha256((run/name).read_bytes()).hexdigest() == digest for name,digest in manifest['cache_hashes'].items())
    assert old_events_preserved and caches_preserved, 'Successful-window preservation check failed.'
    assert job['batch_calls'] <= args.max_calls
    after = httpx.get(base+'/api/health').json()
    report = {'is_mock':False,'video_id':vid,'run_id':run_id,'maximum_model_requests':args.max_calls,
              'before_completed_windows':detail['latest_job']['completed_windows'],
              'after_completed_windows':job['completed_windows'],'status':job['status'],
              'batch_calls':job['batch_calls'],'total_windows':job['total_windows'],
              'failed_windows':job['failed_windows'],'successful_caches_preserved':caches_preserved,
              'old_events_preserved':old_events_preserved,'old_event_count':len(manifest['original_events']),
              'current_event_count':len(document['events']),'split_windows':job.get('split_windows',[]),
              'new_requests_observed':after['usage']['requests']-before['usage']['requests'],
              'human_verdicts_added':0,'note':'Model and schema validation only; visual correctness still needs human review.'}
    (ROOT/f'evidence/recovery-real-{args.report}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

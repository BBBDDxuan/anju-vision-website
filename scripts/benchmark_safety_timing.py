"""Time fresh local scanning and the production streaming Qwen safety cascade.

Uses the configured request ledger and budgets; never prints credentials.
Explicit --allow-cloud is required for paid reviews. Evidence/results stay local.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Settings
from backend.db import Database
from backend.errors import AppError
from backend.hybrid_safety import HybridSafety, atomic_json
from backend.providers.qwen import QwenProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-cloud', action='store_true')
    args = parser.parse_args()
    settings = Settings.from_env()
    db = Database(settings.data_dir)
    hybrid = HybridSafety(settings, db, QwenProvider(settings, db))
    if args.allow_cloud:
        hybrid.ready()
    source = ROOT / 'video' / '10月30日.mp4'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM videos WHERE sha256=? LIMIT 1', (digest,)).fetchone()
    if row is None:
        raise RuntimeError('Import the demo video into the app before benchmarking.')
    video = dict(row)
    video['original_path'] = str(source)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    output = ROOT / 'runtime' / ('safety-timing-' + stamp)
    output.mkdir(exist_ok=False)
    report = {'source': str(source), 'sha256': digest, 'duration_seconds': video['duration_ms']/1000,
              'review_model': settings.review_model, 'billing_mode': settings.billing_mode,
              'before_usage': db.usage_summary(), 'runs': []}
    atomic_json(output / 'timing.json', report)
    for name in (['local_only', 'local_and_qwen'] if args.allow_cloud else ['local_only']):
        run_dir = output / name
        reviews = []
        candidates_at = []
        start = time.perf_counter()
        def review_candidate(candidate):
            seen = round(time.perf_counter() - start, 3)
            candidates_at.append({'type':candidate['event_type'],'video_seconds':candidate['t'],'available_seconds':seen})
            print(json.dumps({'run':name,'candidate':candidate['event_type'],'video_seconds':candidate['t'],'available_seconds':seen}), flush=True)
            if name != 'local_and_qwen':
                return
            if len(reviews) >= 4:
                raise RuntimeError('Benchmark review limit reached (4).')
            t = time.perf_counter()
            result = hybrid.review(candidate, video['id'], 'benchmark_' + stamp)
            entry = {'candidate_id':candidate['id'],'video_seconds':candidate['t'],
                     'elapsed_seconds':round(time.perf_counter()-t,3),'result':result}
            reviews.append(entry)
            atomic_json(run_dir / 'reviews.json', reviews)
            print(json.dumps({'run':name,'verdict':result['verdict'],'seconds':entry['elapsed_seconds'],
                              'usage':result.get('usage',{}).get('usage',{})}),flush=True)
        try:
            candidates = hybrid.scan_video(video, run_dir, on_candidate=review_candidate)
            elapsed = round(time.perf_counter()-start,3)
            scan = json.loads((run_dir/'safety'/'scan.json').read_text('utf-8'))
            entry = {'name':name,'elapsed_seconds':elapsed,'completed':scan['completed'],
                     'candidates':candidates_at,'reviews':reviews,
                     'scan_metrics':{k:v for k,v in scan.items() if k!='candidates'}}
            report['runs'].append(entry)
            print(json.dumps({'run':name,'elapsed_seconds':elapsed,'completed':scan['completed'],'candidates':len(candidates)}),flush=True)
        except AppError as exc:
            report['runs'].append({'name':name,'elapsed_seconds':round(time.perf_counter()-start,3),
                'completed':False,'error':exc.public(),'candidates':candidates_at,'reviews':reviews})
            report['after_usage']=db.usage_summary()
            atomic_json(output/'timing.json',report)
            print(json.dumps({'run':name,'error':exc.public()}),flush=True)
            return 1
        report['after_usage'] = db.usage_summary()
        atomic_json(output / 'timing.json', report)
    print(json.dumps({'report':str(output/'timing.json')},ensure_ascii=True),flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

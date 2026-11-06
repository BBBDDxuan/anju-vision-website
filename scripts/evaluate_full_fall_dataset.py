"""Frozen full CAUCAFall video-level evaluation with bounded real Qwen review.

Consumes an already frozen manifest/protocol in --output. Writes all evidence,
request receipts, CSV and Markdown there. Daily .env configuration is unchanged.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Settings
from backend.db import Database
from backend.errors import AppError
from backend.hybrid_safety import (HybridSafety, atomic_json, confirmed_episode,
                                   detector_environment, duplicate_episode_result)
from backend.providers.qwen import QwenProvider


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def metrics(records, key):
    valid = [r for r in records if r.get('scan_complete') and r.get(key) is not None]
    tp = sum(r['truth_fall'] and r[key] for r in valid)
    fn = sum(r['truth_fall'] and not r[key] for r in valid)
    fp = sum(not r['truth_fall'] and r[key] for r in valid)
    tn = sum(not r['truth_fall'] and not r[key] for r in valid)
    def ratio(n, d):
        return n / d if d else None
    return dict(evaluated=len(valid), missing=len(records)-len(valid), tp=tp, fn=fn, fp=fp, tn=tn,
                recall=ratio(tp,tp+fn), specificity=ratio(tn,tn+fp), precision=ratio(tp,tp+fp),
                accuracy=ratio(tp+tn,len(valid)), f1=ratio(2*tp,2*tp+fp+fn))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-cloud', action='store_true')
    parser.add_argument('--max-new-calls', type=int, default=0)
    args = parser.parse_args()
    out = args.output.resolve()
    protocol = read(out/'protocol.json')
    cases = read(out/'manifest.json')
    assert args.allow_cloud and 0 < args.max_new_calls <= protocol['authorized_max_new_cloud_calls']
    assert not (out/'started.json').exists(), 'Preserve the previous run; explicit recovery is required.'
    for name, digest in protocol['source_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest, name
    base = Settings.from_env()
    db = Database(base.data_dir)
    usage = db.usage_summary()
    assert usage == protocol['usage_before'] and usage['pending'] == 0
    # This Settings instance alone gets the authorized evaluation allowance.
    settings = replace(base, max_daily_calls=usage['requests']+args.max_new_calls,
                       max_calls=args.max_new_calls)
    pipeline = HybridSafety(settings, db, QwenProvider(settings, db))
    pipeline.ready()
    assert pipeline.signature() == protocol['signature']
    atomic_json(out/'started.json', dict(started_at=datetime.now(timezone.utc).isoformat(),
                                        authorized_max_new_calls=args.max_new_calls,
                                        production_daily_cap=base.max_daily_calls,
                                        process_daily_cap=settings.max_daily_calls))
    # Check that model pooling leaves the public scanner's actual evidence intact.
    first = cases[0]
    reference = pipeline.scan_video({'original_path':first['source']}, out/'unpooled-reference')
    atomic_json(out/'unpooled-reference.json', dict(id=first['id'], candidates=reference))
    lock = threading.Lock()
    attempts = 0
    rows = {}
    started = time.perf_counter()

    def persist():
        ordered = [rows[c['id']] for c in cases if c['id'] in rows]
        atomic_json(out/'progress.json', dict(scanned=len(rows), reviewed_videos=sum(r.get('review_complete',False) for r in ordered),
            requests_started=attempts, seconds=round(time.perf_counter()-started,3), records=ordered))

    def review_video(case):
        nonlocal attempts
        folder = out/'clips'/case['id']
        report = read(folder/'safety/scan.json')
        assert report['completed'] and report['frames'] == case['source_frames']
        assert report['source_sha256'] == case['source_sha256']
        candidates = [c for c in report['candidates'] if c['event_type']=='suspected_fall']
        for c in candidates:
            pipeline.validate_candidate(c, folder/'safety')
        if case['id'] == first['id']:
            def evidence(items):
                return [(c['t'],c['details'],[(f['pts_ms'],hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()) for f in c['frames']]) for c in items]
            assert evidence(report['candidates']) == evidence(reference), 'Pooled/unpooled evidence mismatch'
            atomic_json(out/'pooling-verified.json', dict(exact_candidate_metadata_timestamps_and_jpeg_hashes=True,
                opaque_ids_compared=False,reason='Namespace hashes distinguish JSON numeric 10 from 10.0; inference cadence is equal.'))
        row = dict(**case, scan_complete=True, scan_wall_seconds=report['timings']['wall_seconds'],
                   decoded_frames=report['frames'], inferred_frames=report['inferred_frames'],
                   local_candidate=bool(candidates), candidate_count=len(candidates),
                   confirmed=None, review_complete=False, reviews=[], candidates=candidates,
                   candidate_timings=report['candidate_timings'])
        with lock:
            rows[case['id']] = row
            persist()
        reviews = {}
        for candidate in candidates:
            previous = confirmed_episode(candidate, candidates, reviews)
            begin = time.perf_counter()
            if previous:
                result = duplicate_episode_result(candidate, previous)
            else:
                identifier = candidate['id']
                receipt = folder/(identifier+'-review.json')
                assert not receipt.exists(), 'Never automatically retry a dispatched request'
                with lock:
                    if attempts >= args.max_new_calls:
                        result = dict(candidate_id=identifier,event_type='suspected_fall',verdict='unavailable',
                                      reason='本轮授权调用数已达到上限，未发送请求。',cloud_images=False,error='EVALUATION_CALL_LIMIT')
                        permitted = False
                    else:
                        assert db.usage_summary()['requests'] < settings.max_daily_calls
                        attempts += 1
                        atomic_json(receipt, dict(candidate_id=identifier, verdict='unavailable',
                            reason='Request dispatch started; no automatic retry if interrupted.',
                            dispatch_started=True, complete=False))
                        persist()
                        permitted = True
                if permitted:
                    try:
                        result = pipeline.review(candidate, 'full_cauca_'+case['id'], 'full_cauca_'+out.name)
                    except AppError as exc:
                        result = dict(candidate_id=identifier,event_type='suspected_fall',verdict='unavailable',
                                      reason=exc.message,error=exc.code,cloud_images=False)
                    except Exception as exc:
                        result = dict(candidate_id=identifier,event_type='suspected_fall',verdict='unavailable',
                                      reason='Review failed; retained evidence, no retry.',error=type(exc).__name__,cloud_images=False)
            valid_event = pipeline.extraction(candidate,result) is not None
            reviews[candidate['id']] = result
            entry = dict(candidate_id=candidate['id'], candidate_t=candidate['t'],
                         complete_seconds=round(time.perf_counter()-begin,6), result=result,
                         confirmed_event=valid_event)
            atomic_json(folder/(candidate['id']+'-review.json'), dict(**entry,complete=True))
            with lock:
                row['reviews'].append(entry)
                persist()
            print(json.dumps(dict(stage='review',id=case['id'],t=candidate['t'],verdict=result['verdict'],
                                  seconds=entry['complete_seconds'])),flush=True)
        with lock:
            row['review_complete'] = True
            row['confirmed'] = any(r['confirmed_event'] for r in row['reviews'])
            atomic_json(folder/'result.json',row)
            persist()
        return row

    worker = ROOT/'scripts/full_fall_scan_worker.py'
    by_id = {c['id']:c for c in cases}
    futures = []
    with (out/'worker-stderr.log').open('w',encoding='utf-8') as errors, ThreadPoolExecutor(max_workers=2) as pool:
        process = subprocess.Popen([str(settings.safety_python),'-u',str(worker),'--output',str(out),
            '--config',str(settings.safety_model_config)],cwd=ROOT,env=detector_environment(settings.safety_python),
            stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf-8',
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for line in process.stdout:
            message = json.loads(line)
            print(json.dumps(message),flush=True)
            if message['status']=='clip_complete':
                future = pool.submit(review_video, by_id[message['id']])
                futures.append(future)
                if message['id'] == first['id']:
                    # Fail the pooling check before dispatching other reviews.
                    future.result()
            elif message['status']=='clip_error':
                case = by_id[message['id']]
                with lock:
                    rows[case['id']] = dict(**case,scan_complete=False,review_complete=False,
                        local_candidate=None,confirmed=None,scan_error=message['error_type'],reviews=[])
                    persist()
        assert process.wait() == 0, 'Scan worker failed'
        for future in as_completed(futures):
            future.result()
    ordered = [rows[c['id']] for c in cases]
    assert len(ordered)==100 and all(r['scan_complete'] and r['review_complete'] for r in ordered)
    assert (out/'pooling-verified.json').is_file()
    final_usage = db.usage_summary()
    assert final_usage['requests']-usage['requests'] <= args.max_new_calls
    assert final_usage['pending']==0
    for name,digest in protocol['source_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    stats = dict(local=metrics(ordered,'local_candidate'), final=metrics(ordered,'confirmed'))
    actions = {action:dict(local=metrics([r for r in ordered if r['action']==action],'local_candidate'),
                          final=metrics([r for r in ordered if r['action']==action],'confirmed'))
               for action in sorted({r['action'] for r in ordered})}
    results = dict(protocol=protocol,usage_before=usage,usage_after=final_usage,
        new_cloud_calls=final_usage['requests']-usage['requests'], attempted_reviews=attempts,
        elapsed_seconds=round(time.perf_counter()-started,3), metrics=stats,per_action=actions,records=ordered,
        production_daily_cap_after=Settings.from_env().max_daily_calls,
        false_positives=[r['id'] for r in ordered if not r['truth_fall'] and r['confirmed']],
        false_negatives=[r['id'] for r in ordered if r['truth_fall'] and not r['confirmed']])
    atomic_json(out/'results.json',results)
    with (out/'videos.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['id','subject','action','truth_fall','duration_seconds',
                                                'local_candidate','candidate_count','confirmed','scan_wall_seconds'])
        writer.writeheader()
        writer.writerows({k:r.get(k) for k in writer.fieldnames} for r in ordered)
    print(json.dumps(dict(stage='COMPLETE',metrics=stats,new_cloud_calls=results['new_cloud_calls'],
                          false_positives=results['false_positives'],false_negatives=results['false_negatives'])),flush=True)


if __name__=='__main__':
    main()

"""Bounded, user-authorized real Qwen test on the entire 6m33s recording."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'evidence/real-long-qwen.json'


def main():
    report=json.loads(REPORT.read_text(encoding='utf8')) if REPORT.exists() else {'is_mock':False,'source':'video/桌面_逆时针90度.mp4','maximum_new_test_requests':16}
    def save():
        report['updated_at_utc']=datetime.now(timezone.utc).isoformat()
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=180) as client:
        health=client.get('/api/health').json()
        assert health['analysis_ready'] and not health['is_mock']
        report.setdefault('starting_usage',health['usage'])
        if not report.get('video_id'):
            with (ROOT/report['source']).open('rb') as stream:
                response=client.post('/api/videos',files={'file':('完整长视频_桌面_6分33秒.mp4',stream,'video/mp4')})
            response.raise_for_status()
            report['video_id']=response.json()['video_id']
            report['duration_ms']=response.json()['duration_ms']
            save()
        vid=report['video_id']
        report['plan']=client.get(f'/api/videos/{vid}/analysis-plan').json()
        assert report['plan']['total_windows']==14,report['plan']
        if not report.get('job_id'):
            response=client.post(f'/api/videos/{vid}/analyze')
            response.raise_for_status()
            report['job_id']=response.json()['id']
            save()
        while True:
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                job=client.get(f"/api/jobs/{report['job_id']}").json()
                print(job['status'],job['stage'],flush=True)
                if job['status'] not in {'queued','running'}:break
                time.sleep(8)
            report['job']=job
            report['ending_usage']=client.get('/api/health').json()['usage']
            save()
            if job['status']=='paused' and job.get('error',{}).get('code')=='BATCH_LIMIT':
                used=report['ending_usage']['requests']-report['starting_usage']['requests']
                if used+min(4,job['pending_windows'])>report['maximum_new_test_requests']:
                    print('Test request ceiling reached; preserve progress',flush=True)
                    break
                response=client.post(f'/api/videos/{vid}/resume')
                response.raise_for_status()
                continue
            break
        if job['completed_windows']:
            memory=client.get(f'/api/videos/{vid}/events').json()
            assert not memory['is_mock'] and memory['video_duration_ms']>390000
            (ROOT/'data/validation/real-long-memory.json').write_text(json.dumps(memory,ensure_ascii=False,indent=2),encoding='utf8')
            report['events']=len(memory['events'])
            report['entities']=len(memory['entities'])
            report['sampled_frames']=len(memory['frames'])
            report['last_event_ms']=max((e['end_ms'] for e in memory['events']),default=None)
            counts={e['id']:sum(event['subject_id']==e['id'] for event in memory['events']) for e in memory['entities']}
            report['queries']=[]
            for entity in sorted(memory['entities'],key=lambda e:counts[e['id']],reverse=True)[:3]:
                for question in [entity['label']+'最后在哪里出现？',entity['label']+'的移动经过是什么？',entity['label']+'现在还在那里吗？']:
                    answer=client.post(f'/api/videos/{vid}/query',json={'question':question,'entity_id':entity['id']}).json()
                    valid={e['id'] for e in memory['evidence']}
                    assert all(ref in valid for claim in answer['observations']+answer['inferences'] for ref in claim['evidence_ids'])
                    if '现在' in question:assert answer['unknowns']
                    report['queries'].append(answer)
            report['schema_and_references']='PASS'
        report['status']='PASS' if job['status']=='completed' else 'PARTIAL_OR_FAILED'
        report['rmb_cost']=None
        save()
        print(json.dumps({k:report.get(k) for k in ['status','duration_ms','events','entities','sampled_frames','last_event_ms','ending_usage']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()

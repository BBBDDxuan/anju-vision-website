"""Bounded, explicitly opted-in real-model check of the local lighter clip."""
import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-model', action='store_true', help='Authorize up to 3 windows plus one retry each (6 paid requests).')
    args = parser.parse_args()
    if not args.run_model:
        parser.error('Real calls require --run-model.')
    out = Path('data/validation/fire-density-20261021')
    out.mkdir(parents=True, exist_ok=True)
    vid = 'v_adfc02b0829849a296ca7c5de0eda766'
    with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False, timeout=30) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def save(name, value):
            (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')

        before = get('/api/health')
        plan = get(f'/api/videos/{vid}/analysis-plan')
        assert before['analysis_ready'] and before['model'] == 'qwen3.8-max'
        assert before['usage']['pending'] == 0
        assert plan['enhanced_safety_sampling'] and plan['sampling_fps'] == 4
        assert plan['total_windows'] == 3 and plan['max_windows_per_batch'] >= 3
        assert plan['daily_requests_remaining'] >= 6
        for video in get('/api/videos')['videos']:
            detail = get(f"/api/videos/{video['id']}")
            assert (detail.get('latest_job') or {}).get('status') not in {'running', 'queued'}
        save('before-memory.json', get(f'/api/videos/{vid}/events'))
        save('before-alerts.json', get(f'/api/videos/{vid}/alerts'))
        save('plan.json', plan)
        response = client.post(f'/api/videos/{vid}/analyze?auto_continue=false')
        response.raise_for_status()
        job = response.json()
        save('started-job.json', job)
        deadline = time.monotonic()+600
        while job['status'] in {'running', 'queued'}:
            if time.monotonic() > deadline:
                client.post(f'/api/videos/{vid}/pause').raise_for_status()
                raise TimeoutError('Paused after bounded validation deadline.')
            time.sleep(2)
            job = get(f"/api/jobs/{job['id']}")
        memory = get(f'/api/videos/{vid}/events')
        alerts = get(f'/api/videos/{vid}/alerts')
        response = client.post(f'/api/videos/{vid}/query', json={'question': '视频中什么时候出现明火？'})
        response.raise_for_status()
        query = response.json()
        after = get('/api/health')
        events = [e for e in memory['events'] if e['action'] == 'flame']
        summary = {'video_id': vid, 'job_id': job['id'], 'status': job['status'],
                   'requests': after['usage']['requests']-before['usage']['requests'],
                   'flame_events': events, 'flame_alerts': [a for a in alerts['alerts'] if a['action'] == 'flame'],
                   'qa_observations': len(query['observations']), 'frame_count': len(memory['frames'])}
        for name, value in [('job.json', job), ('memory.json', memory), ('alerts.json', alerts),
                            ('query.json', query), ('summary.json', summary)]:
            save(name, value)
        assert summary['requests'] <= 6, summary['requests']
        print(json.dumps({k: v for k, v in summary.items() if k not in {'flame_events', 'flame_alerts'}}, ensure_ascii=False))
        print('Flame events:', [(e['start_ms'], e['end_ms']) for e in events])
        print('Flame alerts:', [(a['start_ms'], a['end_ms']) for a in summary['flame_alerts']])


if __name__ == '__main__':
    main()

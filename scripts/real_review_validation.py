"""One explicitly requested real API review on an imported historical candidate.

No human confirmation is submitted. --run is required and an existing report blocks repeats.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import Settings
from backend.db import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--retry-parameter-fix', action='store_true', help='One explicit retry of a recorded HTTP400 after a parameter fix; preserve original report.')
    args = parser.parse_args()
    settings = Settings.from_env()
    db = Database(settings.data_dir)
    imported = json.loads((settings.data_dir / 'validation/review-demo-import.json').read_text(encoding='utf8'))
    vid = imported['imported'][1]['video_id']
    original, _ = db.raw_memory(vid)
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=240) as client:
        state = client.get(f'/api/videos/{vid}/review').json()
        labels = {e['id']: e['label'] for e in state['entities']}
        event = next(e['original'] for e in state['entries'] if e['original']['action'] == 'put_in' and '笔记本电脑' in labels[e['original']['subject_id']])
        if not args.run:
            print(json.dumps({'video_id': vid, 'event_id': event['id'], 'maximum_paid_requests': 1, 'will_call_model': False}, ensure_ascii=False))
            return
        output = ROOT / 'evidence/review-real-validation.json'
        if args.retry_parameter_fix:
            previous = json.loads(output.read_text(encoding='utf8'))
            if previous.get('error', {}).get('error', {}).get('code') != 'PROVIDER_HTTP':
                raise RuntimeError('No recorded provider parameter failure to retry.')
            output = ROOT / 'evidence/review-real-validation-retry.json'
        if output.exists():
            raise RuntimeError('A real verification report exists; do not duplicate paid calls.')
        report = {'is_mock': False, 'human_verdicts_added': 0, 'video_id': vid, 'event_id': event['id'],
                  'started_at': datetime.now(timezone.utc).isoformat(), 'starting_usage': db.usage_summary()}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        response = client.post(f'/api/videos/{vid}/review/{event["id"]}/verify', json={
            'run_id': state['run_id'], 'expected_revision': state['revision']})
        report['http_status'] = response.status_code
        result = response.json()
        if response.is_success:
            entry = next(e for e in result['entries'] if e['original']['id'] == event['id'])
            report['review_status'] = entry['status']
            report['machine'] = entry['machine']
            report['answer'] = client.post(f'/api/videos/{vid}/query', json={
                'question': '笔记本电脑被放入背包了吗？', 'entity_id': event['subject_id']}).json()
            assert entry['human'] is None
        else:
            report['error'] = result
        report['original_unchanged'] = db.raw_memory(vid)[0] == original
        report['ending_usage'] = db.usage_summary()
        report['paid_requests'] = report['ending_usage']['requests'] - report['starting_usage']['requests']
        assert report['paid_requests'] <= 1 and report['original_unchanged']
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps({k: report.get(k) for k in ['http_status', 'review_status', 'error', 'paid_requests', 'original_unchanged']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

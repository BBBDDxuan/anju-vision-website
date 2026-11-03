"""Bounded real recovery of the reported S08 failure, with immutable-source checks."""
import argparse
import hashlib
import json
import time

import httpx
from playwright.sync_api import sync_playwright

from backend.config import ROOT, Settings
from backend.db import Database
from backend.events import validate_memory

VIDEO = 'v_f8d58a4843804b7bacbfb8ef4d125cf5'
RUN = 'j_6d24695e0b7647d38bb06b2ff9629317'
BASE = 'http://127.0.0.1:8000'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-paid',action='store_true')
    args = parser.parse_args()
    if not args.run_paid:
        parser.error('This performs at most one new paid model request; --run-paid is required.')
    settings = Settings.from_env()
    db = Database(settings.data_dir)
    doc, job = db.raw_memory(VIDEO)
    assert job['id'] == RUN and job['status'] == 'partial'
    assert len(job['failed_windows']) == 1 and job['failed_windows'][0]['index'] == 0
    assert job['failed_windows'][0]['error']['code'] == 'MODEL_OUTPUT_INVALID'
    assert httpx.get(BASE+'/api/health').json()['usage']['pending'] == 0
    old_reviews = digest(db.review_records(VIDEO))
    cache = settings.data_dir/'runs'/RUN/'window-00001.json'
    old_cache = hashlib.sha256(cache.read_bytes()).hexdigest()
    backup = settings.data_dir/'s08-recovery-backup'
    backup.mkdir(exist_ok=False)
    (backup/'memory-before.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf8')
    (backup/'job-before.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf8')
    (backup/'window-00001.json').write_bytes(cache.read_bytes())
    before = db.usage_summary()['requests']
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome',headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1050})
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.goto(f'{BASE}/workspace?video={VIDEO}')
        warning = page.locator('.warning').filter(has_text='识别缺口：00:00.0–00:08.0')
        warning.wait_for()
        warning.scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/s08-missing-window-before.png'))
        started = httpx.post(f'{BASE}/api/videos/{VIDEO}/resume?max_calls=1',timeout=15)
        started.raise_for_status()
        assert started.json()['id'] == RUN
        deadline = time.monotonic()+180
        while True:
            current = httpx.get(f'{BASE}/api/jobs/{RUN}').json()
            if current['status'] not in {'running','queued'}:
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Monitoring timed out; do not restart an in-flight model request.')
            time.sleep(1)
        updated, _ = db.raw_memory(VIDEO)
        validate_memory(updated)
        by_id = {e['id']:e for e in updated['events']}
        assert all(by_id.get(e['id']) == e for e in doc['events'])
        assert hashlib.sha256(cache.read_bytes()).hexdigest() == old_cache
        assert digest(db.review_records(VIDEO)) == old_reviews
        calls = db.usage_summary()['requests']-before
        assert calls == current['batch_calls'] == 1
        falls = [e for e in updated['events'] if e['action'] == 'fall']
        page.reload()
        page.locator('.coverage strong').filter(has_text='全片已处理').wait_for() if current['status'] == 'completed' else None
        if falls:
            alert = page.locator('.safety-alert').filter(has_text='疑似跌倒')
            alert.wait_for()
            assert alert.count() == 1
            alert.get_by_role('button',name='查看视频证据 ↗',exact=True).click()
            page.wait_for_function('(seconds) => Math.abs(document.querySelector("video").currentTime-seconds)<.3',arg=falls[0]['start_ms']/1000)
            alert.scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/s08-format-recovered.png'))
        assert not errors, errors
        browser.close()
    report = {'is_mock':False,'video_id':VIDEO,'run_id':RUN,'new_paid_requests':calls,
              'before_status':job['status'],'after_status':current['status'],
              'completed_windows':current['completed_windows'],'total_windows':current['total_windows'],
              'old_events_preserved':True,'successful_cache_preserved':True,'reviews_unchanged':True,
              'failed_ranges_visible':True,'fall_events':[{'start_ms':e['start_ms'],'end_ms':e['end_ms'],
                'epistemic_type':e['epistemic_type'],'model_score':e['model_score'],'evidence_ids':e['evidence_ids']} for e in falls],
              'window_recoveries':current.get('window_recoveries',{}),'page_errors':errors,
              'limitations':'One saved sample only; not evidence of general fall-detection accuracy.'}
    (ROOT/'evidence/s08-real-format-recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

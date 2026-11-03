"""Check saved real model results and browser grouping; never call a model."""
import hashlib
import json
import sys

import httpx
from playwright.sync_api import sync_playwright

from backend.config import ROOT, Settings
from backend.db import Database

VIDEO = 'v_ec8b878df3004944abca795b6ed142ad'
BASE = 'http://127.0.0.1:8000'
SNAPSHOT = ROOT / 'evidence/dedup-before.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main():
    db = Database(Settings.from_env().data_dir)
    health = httpx.get(BASE+'/api/health').json()
    if '--before' in sys.argv:
        assert health['usage']['pending'] == 0
        videos = db.list_videos()
        assert all((db.latest_job(v['id']) or {}).get('status') not in {'queued','running'} for v in videos)
        records = {v['id']: {'raw':digest(db.raw_memory(v['id'])[0]),
                              'reviews':digest(db.review_records(v['id']))}
                   for v in videos if v['active_run']}
        SNAPSHOT.write_text(json.dumps({'records':records,'requests':health['usage']['requests']}),encoding='utf8')
        print(json.dumps({'idle':True,'saved_memories':len(records),'requests':health['usage']['requests']}))
        return
    before = json.loads(SNAPSHOT.read_text(encoding='utf8'))
    memory = httpx.get(f'{BASE}/api/videos/{VIDEO}/events?grouped=true').json()
    falls = [e for e in memory['events'] if e['action'] == 'fall']
    assert len(falls) == 2, 'Expected the existing two-source real fall sample'
    ids = {e['id'] for e in falls}
    groups = [g for g in memory['event_groups'] if ids & set(g['event_ids'])]
    assert len(groups) == 1 and set(groups[0]['event_ids']) == ids
    assert (groups[0]['start_ms'],groups[0]['end_ms']) == (5000,7000)
    alerts = httpx.get(f'{BASE}/api/videos/{VIDEO}/alerts').json()['alerts']
    assert len([a for a in alerts if a['action'] == 'fall']) == 1
    answer = httpx.post(f'{BASE}/api/videos/{VIDEO}/query',json={
        'question':'人物1什么时候跌倒？','entity_id':falls[0]['subject_id']}).json()
    assert len(answer['observations']) == 1 and set(answer['observations'][0]['event_ids']) == ids
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome',headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1050})
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.goto(f'{BASE}/workspace?video={VIDEO}')
        fall = page.locator('.event-group').filter(has_text='出现疑似跌倒过程')
        fall.wait_for()
        assert fall.count() == 1 and fall.get_attribute('data-source-count') == '2'
        assert fall.get_attribute('data-confidence') == 'medium'
        assert page.locator('.safety-alert').filter(has_text='疑似跌倒').count() == 1
        fall.locator('summary').click()
        assert fall.locator('.event-source').count() == 2
        assert fall.locator('.reference').count() == 2
        fall.locator('.reference').first.click()
        page.wait_for_function('() => Math.abs(document.querySelector("video").currentTime-5)<.3')
        fall.scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/dedup-real.png'))
        page.get_by_role('button',name='踪迹回溯',exact=True).click()
        page.locator('#entity-select').select_option(falls[0]['subject_id'])
        replay = page.locator('.replay-entry').filter(has_text='出现疑似跌倒过程')
        replay.wait_for()
        assert replay.count() == 1 and replay.get_attribute('data-source-count') == '2'
        assert replay.get_attribute('data-confidence') == 'medium'
        replay.locator('summary').first.click()
        assert len(replay.locator('.event-group-sources .reference').all()) == 2
        page.get_by_label('你想找回什么线索？',exact=True).fill('人物1什么时候跌倒？')
        page.get_by_role('button',name='提问',exact=True).click()
        page.locator('.claim').filter(has_text='直接观察').wait_for()
        assert page.locator('.claim').filter(has_text='直接观察').count() == 1
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()
    for video, record in before['records'].items():
        assert digest(db.raw_memory(video)[0]) == record['raw']
        assert digest(db.review_records(video)) == record['reviews']
    assert httpx.get(BASE+'/api/health').json()['usage']['requests'] == before['requests']
    report = {'saved_real_model_results':True,'new_model_calls':0,'raw_memories_unchanged':True,
              'reviews_unchanged':True,'fall_source_records':2,'displayed_fall_occurrences':1,
              'range_ms':[5000,7000],'passed':['timeline','source_expansion','evidence_seek',
              'replay','query','persisted_alerts','yellow_preserved','mobile_no_overflow'], 'page_errors':errors}
    (ROOT/'evidence/dedup-real.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()

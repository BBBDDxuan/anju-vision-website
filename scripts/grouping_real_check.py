"""Verify grouped saved genuine results without model calls or human verdicts."""
import hashlib
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from backend.config import ROOT, Settings
from backend.db import Database


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf8')).hexdigest()


def main():
    settings = Settings.from_env()
    db = Database(settings.data_dir)
    backup = Path((settings.data_dir/'grouping-backup-path.txt').read_text(encoding='utf8'))
    manifest = json.loads((backup/'manifest.json').read_text(encoding='utf8'))
    base = 'http://127.0.0.1:8000'
    before = httpx.get(base+'/api/health').json()
    results = []
    for record in manifest['records']:
        vid = record['video_id']
        raw, job = db.raw_memory(vid)
        assert digest(raw) == record['raw_hash']
        assert digest(db.review_records(vid)) == record['review_hash']
        result = httpx.get(f'{base}/api/videos/{vid}/events?grouped=true').json()
        groups = result['event_groups']
        source_ids = [identifier for g in groups for identifier in g['event_ids']]
        assert len(source_ids) == len(set(source_ids)) == len(result['events'])
        assert set(source_ids) == {e['id'] for e in result['events']}
        answer = httpx.post(f'{base}/api/videos/{vid}/query',json={'question':'笔记本电脑最后在哪里出现？'}).json()
        assert answer == record['last_seen_answer']
        results.append({'video_id':vid,'video_name':record['name'],'raw_events':len(result['events']),
                        'display_items':len(groups),'raw_memory_unchanged':True,'reviews_unchanged':True,
                        'precise_last_seen_answer_unchanged':True})
    selected = 'v_review_c87ee0f8a562ad381f5d'
    item = next(r for r in results if r['video_id'] == selected)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome',headless=True)
        page = browser.new_page(viewport={'width':1600,'height':1100})
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.goto(base+'/?video='+selected)
        page.get_by_text(f"{item['raw_events']} 条记录",exact=True).wait_for()
        page.get_by_text(f"当前显示 {item['display_items']} 项 · 对应 {item['raw_events']} 条原始记录",exact=True).wait_for()
        group = page.locator('.event-group').first
        group.locator('summary').click()
        first = group.locator('.event-source').first
        event_id = first.get_attribute('data-event-id')
        first.locator('.reference').first.click()
        page.locator('.evidence-view img').first.wait_for()
        first.get_by_role('button',name='人工核对此条',exact=True).click()
        page.wait_for_function('(id) => document.querySelector("#review-event").value === id',arg=event_id)
        assert page.locator('.review-panel').evaluate('(node) => node.open')
        group.scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/grouping-real-desktop.png'),animations='disabled')
        page.get_by_role('button',name='展开全部原始事件',exact=True).click()
        page.get_by_text(f"当前显示 {item['raw_events']} 项 · 对应 {item['raw_events']} 条原始记录",exact=True).wait_for()
        page.get_by_role('button',name='折叠重复记录',exact=True).click()
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('.event-group').first.scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/grouping-real-mobile.png'),animations='disabled')
        assert not errors,errors
        browser.close()
    after = httpx.get(base+'/api/health').json()
    assert before['usage']['requests'] == after['usage']['requests'] == manifest['usage']['requests']
    for record in manifest['records']:
        assert digest(db.raw_memory(record['video_id'])[0]) == record['raw_hash']
        assert digest(db.review_records(record['video_id'])) == record['review_hash']
    report = {'saved_results_are_real':True,'new_model_calls':0,'new_human_verdicts':0,'videos':results,
              'passed':['all_sources_retained','original_memories_unchanged','review_history_unchanged',
                        'precise_last_seen_unchanged','expand_and_fold','evidence_navigation','source_review_navigation',
                        'mobile_no_overflow'],'page_errors':errors}
    (ROOT/'evidence/grouping-real-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

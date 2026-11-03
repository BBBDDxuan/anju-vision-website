"""Read-only UI/QA/evidence audit of the saved real fall sample suite."""
from collections import Counter
import hashlib
import json

import httpx
from playwright.sync_api import sync_playwright

from backend.config import ROOT

BASE = 'http://127.0.0.1:8000'
OUT = ROOT/'data/validation/fall-suite-20261020'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def main():
    suite = json.loads((OUT/'suite.json').read_text(encoding='utf8'))
    results, errors = [], []
    before = httpx.get(BASE+'/api/health').json()['usage']['requests']
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome',headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1050})
        page.on('pageerror',lambda error:errors.append(str(error)))
        for record in suite['clips']:
            vid = record['video_id']
            url = f'{BASE}/api/videos/{vid}/events'
            raw = digest(httpx.get(url).json())
            page.goto(f'{BASE}/workspace?video={vid}')
            page.locator('.event.priority-event').first.wait_for()
            page.locator('.safety-alert-heading strong').filter(has_text='疑似跌倒').first.wait_for()
            event_rows = page.locator('.event').evaluate_all('''nodes => nodes.map(node => ({
                title:node.querySelector(':scope > div > p')?.textContent || '',
                sources:Number(node.dataset.sourceCount),confidence:node.dataset.confidence,
                time:node.querySelector(':scope > time')?.textContent || ''}))''')
            fall_rows = [e for e in event_rows if '出现疑似跌倒过程' in e['title']]
            red_alerts = page.locator('.safety-alert-heading strong').filter(has_text='疑似跌倒').count()
            assert len(fall_rows) == red_alerts == 1, (record['subject'],fall_rows,red_alerts)
            alert = page.locator('.safety-alert').filter(has_text='疑似跌倒').first
            alert.get_by_role('button',name='查看视频证据 ↗',exact=True).click()
            start = record['fall_events'][0]['start_ms']/1000
            page.wait_for_function('(s) => Math.abs(document.querySelector("video").currentTime-s)<.3',arg=start)
            page.locator('.safety-frames img').first.wait_for()
            page.wait_for_function('() => [...document.querySelectorAll(".safety-frames img")].every(img => img.complete && img.naturalWidth>0)')
            if record['subject'] in {1,4,9}:
                alert.scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/f'S{record["subject"]:02d}-browser.png'))
            partial_warning = page.locator('.warning').filter(has_text='识别缺口').count()>0
            page.get_by_label('你想找回什么线索？',exact=True).fill('视频中什么时候有人跌倒？')
            with page.expect_response(f'{BASE}/api/videos/{vid}/query') as response:
                page.get_by_role('button',name='提问',exact=True).click()
            global_query = response.value.json()
            page.locator('.answer-result').wait_for()
            global_claims = len(global_query['observations'])+len(global_query['inferences'])
            # Record the global-query defect rather than hiding it by selecting
            # an actor first. Then verify the documented scoped workaround.
            person = next(e for e in record['person_entities'] if e['id'] == record['fall_events'][0]['subject_id'])
            page.get_by_label('你想找回什么线索？',exact=True).fill(person['label']+'什么时候跌倒？')
            page.get_by_role('button',name='提问',exact=True).click()
            page.locator('.claim').filter(has_text='出现疑似跌倒过程').first.wait_for()
            claim_count = page.locator('.claim').filter(has_text='出现疑似跌倒过程').count()
            assert claim_count == 1
            page.get_by_role('button',name='踪迹回溯',exact=True).click()
            page.locator('#entity-select').select_option(record['fall_events'][0]['subject_id'])
            replay = page.locator('.replay-entry').filter(has_text='出现疑似跌倒过程')
            replay.wait_for()
            assert replay.count() == 1
            repeated = [title for title,count in Counter(e['title'] for e in event_rows).items() if title and count>1]
            result = {'subject':record['subject'],'video_id':vid,'fall_timeline_rows':len(fall_rows),
                      'fall_alerts':red_alerts,'fall_query_claims':claim_count,'fall_replay_entries':replay.count(),
                      'fall_display':fall_rows[0],'partial_warning_visible':partial_warning,
                      'global_query_status':global_query['status'],'global_query_claims':global_claims,
                      'global_query_issue':global_claims == 0,
                      'potential_repeated_primary_titles':repeated,'evidence_seek_pass':True,'evidence_images_pass':True}
            assert digest(httpx.get(url).json()) == raw
            results.append(result)
            print(json.dumps(result,ensure_ascii=False),flush=True)
        assert not errors,errors
        browser.close()
    assert httpx.get(BASE+'/api/health').json()['usage']['requests'] == before
    report = {'is_mock':False,'new_model_calls':0,'raw_memories_unchanged':True,
              'clips_checked':len(results),'results':results,'page_errors':errors}
    (OUT/'browser-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'clips_checked':len(results),'page_errors':errors,'new_model_calls':0}),flush=True)


if __name__ == '__main__':
    main()

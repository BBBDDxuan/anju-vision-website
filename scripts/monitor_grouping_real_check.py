"""Read-only regression check of saved mixed-color monitor observations."""
import hashlib
import json

import httpx
from playwright.sync_api import sync_playwright

from backend.config import ROOT

BASE = 'http://127.0.0.1:8000'
VIDEO = 'v_f8d58a4843804b7bacbfb8ef4d125cf5'


def main():
    url = f'{BASE}/api/videos/{VIDEO}/events'
    digest = lambda value: hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    memory = httpx.get(url).json()
    before = digest(memory)
    requests = httpx.get(BASE+'/api/health').json()['usage']['requests']
    subject = next(e['id'] for e in memory['entities'] if e['label'] == '电脑显示器')
    sources = [e for e in memory['events'] if e['subject_id'] == subject and e['action'] == 'seen_at']
    assert len(sources) == 2
    assert any(e['uncertainty_reasons'] for e in sources) and any(not e['uncertainty_reasons'] for e in sources)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome',headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1050})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'{BASE}/workspace?video={VIDEO}')
        monitor = page.locator('.event').filter(has_text='电脑显示器')
        monitor.wait_for()
        assert monitor.count() == 1 and monitor.get_attribute('data-source-count') == '2'
        assert monitor.get_attribute('data-confidence') == 'medium'
        assert '00:00.0' in monitor.inner_text() and '00:10.0' in monitor.inner_text()
        monitor.locator('summary').click()
        assert monitor.locator('.event-source').count() == 2
        assert monitor.locator('.reference').count() == 2
        monitor.locator('.reference').nth(1).click()
        page.wait_for_function('() => Math.abs(document.querySelector("video").currentTime-6)<.3')
        monitor.scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/monitor-grouping-real.png'))
        page.get_by_role('button',name='展开全部可见事件',exact=True).click()
        assert page.locator('.event').filter(has_text='电脑显示器').count() == 2
        page.get_by_role('button',name='折叠重复记录',exact=True).click()
        assert page.locator('.event').filter(has_text='电脑显示器').count() == 1
        page.get_by_role('button',name='踪迹回溯',exact=True).click()
        page.locator('#entity-select').select_option(subject)
        replay = page.locator('.replay-entry')
        replay.wait_for()
        assert replay.count() == 1 and replay.get_attribute('data-source-count') == '2'
        assert replay.get_attribute('data-confidence') == 'medium'
        assert '不表示期间始终在场' in replay.inner_text()
        replay.locator('summary').click()
        assert replay.locator('.event-group-sources .reference').count() == 2
        assert not errors, errors
        browser.close()
    assert digest(httpx.get(url).json()) == before
    assert httpx.get(BASE+'/api/health').json()['usage']['requests'] == requests
    report = {'saved_real_model_results':True,'new_model_calls':0,'raw_memory_unchanged':True,
              'source_records':2,'timeline_rows':1,'replay_rows':1,'confidence':'medium',
              'passed':['mixed_color_grouping','source_expansion','evidence_seek','fold_toggle',
                        'replay_grouping','sampled_observation_boundary'],'page_errors':errors}
    (ROOT/'evidence/monitor-grouping-real.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()

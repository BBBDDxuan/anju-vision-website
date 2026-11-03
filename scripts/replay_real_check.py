"""Read-only title check against a saved event; no new analysis/model calls."""
import hashlib
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    base = 'http://127.0.0.1:8000'
    video = 'v_ea9e847b90034dbfa32a2731f93c6ba5'
    event_id = 'e_3c41e730afd14f238ef74a7fdf7484e1'
    url = f'{base}/api/videos/{video}/events'
    memory = httpx.get(url).json()
    digest = lambda data: hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    original = digest(memory)
    event = next(e for e in memory['events'] if e['id'] == event_id)
    before = httpx.get(base+'/api/health').json()['usage']['requests']
    replay = httpx.get(f'{base}/api/videos/{video}/replay', params={'entity_id': event['subject_id']}).json()
    entry = next(e for e in replay['entries'] if e['id'] == event_id)
    assert 'condition' not in entry
    assert entry['event']['uncertainty_reasons'] == event['uncertainty_reasons']
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width':1440, 'height':1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{base}/workspace?video={video}')
        page.get_by_role('button', name='踪迹回溯', exact=True).click()
        page.locator('#entity-select').select_option(event['subject_id'])
        title = page.locator('.replay-entry > div > p').filter(has_text='可能：白色保温杯 被放在储物柜上').first
        title.wait_for()
        card = title.locator('..')
        details = card.locator('details')
        assert details.count() == 1 and details.get_attribute('open') is None
        details.locator('summary').click()
        assert '看到的前后变化' in details.inner_text()
        assert card.locator('.evidence-links button, .evidence-ref, a, button').count() > 0
        details.locator('summary').click()
        title.scroll_into_view_if_needed()
        page.screenshot(path=str(root/'evidence/replay-short-title.png'))
        assert 'LifeReplay' not in page.locator('body').inner_text()
        assert not errors, errors
        browser.close()
    assert digest(httpx.get(url).json()) == original
    assert httpx.get(base+'/api/health').json()['usage']['requests'] == before
    result = {'video_id':video, 'event_id':event_id, 'title':'可能：白色保温杯 被放在储物柜上',
              'reasons_expandable':True, 'original_events_unchanged':True, 'new_model_calls':0}
    (root/'evidence/replay-short-title.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

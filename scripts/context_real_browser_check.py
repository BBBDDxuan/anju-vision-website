"""Read-only UI check on saved genuine results: zero model calls or human verdicts."""
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    base = 'http://127.0.0.1:8000'
    video_id = 'v_review_c87ee0f8a562ad381f5d'
    event_id = 'e_76e8edfa1977486093b5296e78c27a0e'
    before = httpx.get(base + '/api/health').json()
    assert before['model'] == 'qwen3.8-max' and before['analysis_ready']
    memory = httpx.get(f'{base}/api/videos/{video_id}/events').json()
    event = next(e for e in memory['events'] if e['id'] == event_id)
    assert event['action'] == 'seen_at' and event['epistemic_type'] == 'inferred'
    source = {r['id']: r for r in memory['evidence']}
    refs = [fid for rid in event['evidence_ids'] for fid in source[rid]['frame_ids']]
    for frame_id in refs:
        response = httpx.get(f'{base}/media/frames/{video_id}/{frame_id}')
        assert response.status_code == 200 and response.headers['content-type'].startswith('image/jpeg')
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{base}/?video={video_id}')
        page.get_by_text('13 条记录', exact=True).wait_for()
        assert 'qwen3.8-max' in page.locator('.privacy').inner_text()
        page.locator('.review-panel > summary').click()
        page.locator('#review-event').select_option(event_id)
        page.locator('.event-review .tag').filter(has_text='上下文推测').wait_for()
        page.get_by_label('你想找回什么线索？', exact=True).fill('笔记本电脑的经过是什么？')
        page.get_by_role('button', name='提问', exact=True).click()
        claim = page.locator('.answer-result .inference').filter(has_text='上下文推测')
        claim.wait_for()
        assert not any('放入' in text and '电脑' in text for text in page.locator('.answer-result .claim:not(.inference):not(.unknown)').all_inner_texts())
        claim.get_by_role('button').first.click()
        page.wait_for_function('() => document.querySelector("video").currentTime >= 370')
        page.wait_for_function('() => [...document.querySelectorAll(".evidence-view img")].every(i => i.complete && i.naturalWidth > 0)')
        page.locator('.answer-result').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT / 'evidence/context-real-desktop.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
        page.locator('.answer-result').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT / 'evidence/context-real-mobile.png'), animations='disabled')
        assert not errors, errors
        browser.close()
    after = httpx.get(base + '/api/health').json()
    assert before['usage']['requests'] == after['usage']['requests']
    report = {'is_mock': False, 'new_paid_calls': 0, 'new_human_verdicts': 0, 'model': after['model'],
              'passed': ['model_badge', 'saved_context_timeline', 'separate_qa_hypothesis', 'no_observed_laptop_put_in',
                         'source_images_load', 'evidence_video_seek', 'mobile_no_overflow'], 'page_errors': errors}
    (ROOT / 'evidence/context-real-browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()

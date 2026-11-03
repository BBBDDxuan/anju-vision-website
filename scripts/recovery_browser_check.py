"""Read-only UI check of recovered real results; no model calls or human verdicts."""
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    base = 'http://127.0.0.1:8000'
    video_id = 'v_review_c87ee0f8a562ad381f5d'
    before = httpx.get(base+'/api/health').json()
    review = httpx.get(f'{base}/api/videos/{video_id}/review').json()
    detail = httpx.get(f'{base}/api/videos/{video_id}').json()
    assert detail['latest_job']['status'] == 'completed'
    reviewed = sum(bool(e['human'] and e['human']['decision'] != 'reset') for e in review['entries'])
    count = len(review['entries'])
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1600, 'height': 1100})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base+'/?video='+video_id)
        page.wait_for_function('() => document.querySelector("video").readyState >= 2')
        page.locator('summary').filter(has_text='场景标定与人工复核').click()
        page.get_by_text(f'人工已审查 {reviewed} / {count} 条', exact=True).wait_for()
        page.get_by_role('button', name='下一条未审查事件', exact=True).click()
        assert page.locator('#review-event').input_value()
        page.get_by_role('button', name='补充当前片段证据（免费）', exact=True).wait_for()
        assert '数据盘可用' not in page.locator('body').inner_text()
        assert '正在回放已保存的真实 Qwen 分析记录' not in page.locator('body').inner_text()
        page.get_by_role('button', name='补充当前片段证据（免费）', exact=True).scroll_into_view_if_needed()
        page.wait_for_function('''() => [...document.querySelectorAll('.review-frames img')]
            .filter(img => img.getBoundingClientRect().top < innerHeight && img.getBoundingClientRect().bottom > 0)
            .every(img => img.complete && img.naturalWidth > 0)''')
        page.screenshot(path=str(ROOT/'evidence/recovery-review-desktop.png'), animations='disabled')
        page.get_by_label('你想找回什么线索？', exact=True).fill('笔记本电脑最后在哪里出现？')
        page.get_by_role('button', name='提问', exact=True).click()
        page.locator('.answer-result').wait_for()
        assert page.locator('.answer-result').inner_text().strip()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('.answer-result').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/recovery-review-mobile.png'), animations='disabled')
        assert not errors, errors
        browser.close()
    after = httpx.get(base+'/api/health').json()
    current_review = httpx.get(f'{base}/api/videos/{video_id}/review').json()
    assert before['usage']['requests'] == after['usage']['requests']
    assert review['revision'] == current_review['revision']
    report = {'saved_results_are_real': True, 'new_model_calls': 0, 'new_human_verdicts': 0,
              'completed_windows': detail['latest_job']['completed_windows'], 'event_count': count,
              'human_reviewed_count': reviewed, 'passed': ['real_recovered_results', 'review_progress',
              'next_unreviewed', 'free_evidence_entry', 'removed_diagnostic_banners', 'saved_memory_qa',
              'real_evidence_images_loaded', 'mobile_no_overflow'], 'page_errors': errors}
    (ROOT/'evidence/recovery-real-browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()

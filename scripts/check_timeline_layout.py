"""Read-only layout regression against saved genuine results; never starts analysis or review."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    errors, mutations, cases = [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: mutations.append(r.url) if r.method == 'POST' else None)
        page.goto('http://127.0.0.1:8000/?video=v_58587d539bdf45b7af59f3e1b957bb37')
        page.locator('.event-filters').wait_for()
        assert '数据盘可用' not in page.locator('body').inner_text()
        assert '正在回放已保存的真实 Qwen 分析记录' not in page.locator('body').inner_text()
        page.locator('.event-filters button').filter(has_text='推测').click()
        assert page.locator('.event').count() > 0
        for width in [1440, 900, 390, 320]:
            page.set_viewport_size({'width': width, 'height': 1000})
            if width <= 1024:
                page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
            boxes = page.locator('.event').evaluate_all('''nodes => nodes.map(node => {
                const tag=node.querySelector(':scope > .tag').getBoundingClientRect();
                const content=node.querySelector(':scope > div').getBoundingClientRect();
                const event=node.getBoundingClientRect();
                return {tagTop:tag.top,tagBottom:tag.bottom,contentBottom:content.bottom,eventBottom:event.bottom};
            })''')
            assert all(b['tagTop'] >= b['contentBottom'] - .5 and b['tagBottom'] <= b['eventBottom']+.5 for b in boxes), boxes
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            cases.append({'width': width, 'checked_tags': len(boxes), 'overlap': False})
            if width in {1440, 390}:
                page.locator('.event').first.screenshot(path=str(ROOT/f'evidence/timeline-tags-{width}.png'), animations='disabled')
        assert not mutations and not errors, (mutations, errors)
        browser.close()
    report={'passed':True,'removed_notices_verified':True,'cases':cases,'new_analysis_or_review_requests':0,'page_errors':errors}
    (ROOT/'evidence/timeline-layout-regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Read-only check of the responsive workspace against saved genuine video results; no inference calls."""
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    base = 'http://127.0.0.1:8000'
    before = httpx.get(base+'/api/health').json()
    assert before['limits']['max_video_seconds'] == 86400
    assert before['limits']['max_upload_mb'] == 65536
    errors = []
    vid = 'v_review_58f7c31040182a6c3b93'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1600, 'height': 1100})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base+'/?video='+vid)
        page.get_by_text('25 条记录', exact=True).wait_for()
        page.wait_for_function('() => document.querySelector("video").readyState >= 2')
        assert page.locator('.event').count() == 20
        page.get_by_role('button', name='下一页', exact=True).click()
        assert page.locator('.event').count() == 5
        page.get_by_role('button', name='上一页', exact=True).click()
        page.get_by_label('按录像小时筛选', exact=True).select_option('0')
        assert page.locator('.event').count() == 20
        page.get_by_label('按录像小时筛选', exact=True).select_option('all')
        page.get_by_label('搜索录像', exact=True).fill('柜格')
        assert page.locator('.library-item').count() == 1
        page.get_by_label('搜索录像', exact=True).fill('')
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(ROOT/'evidence/day-ui-desktop.png'), animations='disabled')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('button', name='打开录像库', exact=True).click()
        page.wait_for_function('() => Math.abs(document.querySelector(".sidebar").getBoundingClientRect().left) < 1')
        page.screenshot(path=str(ROOT/'evidence/day-ui-mobile-library.png'), animations='disabled')
        page.get_by_role('button', name='使用说明与接入计划', exact=True).click()
        page.get_by_role('dialog').wait_for()
        assert '尚未接入' in page.get_by_role('dialog').inner_text()
        page.get_by_role('button', name='关闭使用说明', exact=True).click()
        page.get_by_role('button', name='关闭录像库', exact=True).click()
        page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(ROOT/'evidence/day-ui-mobile.png'), animations='disabled')
        nav = page.get_by_role('navigation', name='手机工作台导航')
        nav.get_by_role('link', name='时间线', exact=True).click()
        page.get_by_label('搜索事件物品或动作', exact=True).fill('电脑')
        assert 0 < page.locator('.event').count() < 20
        nav.get_by_role('link', name='问答', exact=True).click()
        page.get_by_label('你想找回什么线索？', exact=True).fill('笔记本电脑最后在哪里出现？')
        page.get_by_role('button', name='提问', exact=True).click()
        page.locator('.answer-result').wait_for()
        page.locator('.answer-result').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/day-ui-mobile-answer.png'), animations='disabled')
        assert not errors, errors
        browser.close()
    after = httpx.get(base+'/api/health').json()
    assert before['usage']['requests'] == after['usage']['requests']
    report = {'saved_results_are_real': True, 'new_model_calls': 0, 'new_human_verdicts': 0,
              'full_24h_video_tested': False, 'max_video_seconds': after['limits']['max_video_seconds'],
              'passed': ['24h_config', 'desktop_library_search', 'event_pagination_20_5', 'hour_filter',
                         'mobile_drawer', 'planned_camera_features_disclosed', 'mobile_bottom_navigation',
                         'mobile_event_search', 'saved_evidence_qa', 'no_horizontal_overflow'], 'page_errors': errors}
    (ROOT/'evidence/day-ui-browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()

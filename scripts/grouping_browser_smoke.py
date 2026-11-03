"""Exercise evidence grouping and individual reviews with explicitly synthetic video/model."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import cv2
import httpx
import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = Path('D:/MemoryOSHomePrep/grouping-browser')/uuid4().hex
    work.mkdir(parents=True)
    base = 'http://127.0.0.1:8012'
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',8012)) == 0:
            raise RuntimeError('Test port 8012 is occupied; do not terminate another process.')
    source = work/'SYNTHETIC_GROUPING_ONLY.mp4'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 10, (640,360))
    assert writer.isOpened()
    for i in range(120):
        frame = np.full((360,640,3), (34,59,41), np.uint8)
        cv2.putText(frame, 'SYNTHETIC GROUPING TEST ONLY', (15,60), cv2.FONT_HERSHEY_SIMPLEX, .8, (230,240,210), 1)
        writer.write(frame)
    writer.release()
    env = dict(os.environ, SHIJI_BROWSER_TEST_DIR=str(work/'data'), PYTHONUTF8='1')
    log = (work/'server.log').open('w',encoding='utf8')
    process = subprocess.Popen([sys.executable,'-m','uvicorn','tests.grouping_browser_server:create_test_app','--factory',
                                '--host','127.0.0.1','--port','8012'],cwd=ROOT,env=env,stdout=log,stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    errors = []
    try:
        for _ in range(100):
            try:
                if httpx.get(base+'/api/health',timeout=1).status_code == 200: break
            except httpx.RequestError: pass
            time.sleep(.1)
        else: raise RuntimeError('Test server did not start')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome',headless=True)
            page = browser.new_page(viewport={'width':1440,'height':1080})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/workspace')
            page.get_by_label('上传 MP4').set_input_files(str(source))
            page.get_by_role('button',name='开始 AI 分析',exact=True).click()
            page.get_by_text('9 条记录',exact=True).wait_for()
            assert page.locator('.event').count() == 5
            page.get_by_text('当前显示 5 项 · 对应 9 条原始记录',exact=True).wait_for()
            vid = page.locator('#video-select').input_value()
            original = httpx.get(f'{base}/api/videos/{vid}/events').json()
            first_group = page.locator('.event-group').first
            first_group.locator('summary').click()
            sources = first_group.locator('.event-source')
            assert sources.count() == 2
            source_id = sources.first.get_attribute('data-event-id')
            sources.first.get_by_role('button',name='人工核对此条',exact=True).click()
            page.wait_for_function('(id) => document.querySelector("#review-event").value === id',arg=source_id)
            assert page.locator('.review-panel').evaluate('(node) => node.open')
            page.get_by_text('人工已审查 0 / 9 条',exact=True).wait_for()
            page.locator('.event-filters button').filter(has_text='观察').click()
            # Unknown at 8s is excluded from display, but must still split 6s/9s observations.
            assert page.locator('.event').count() == 4
            page.locator('.event-filters button').filter(has_text='全部').click()
            page.get_by_role('button',name='展开全部原始事件',exact=True).click()
            assert page.locator('.event').count() == 9
            page.get_by_role('button',name='折叠重复记录',exact=True).click()
            assert page.locator('.event').count() == 5
            page.get_by_label('你想找回什么线索？',exact=True).fill('测试钥匙的经过是什么？')
            page.get_by_role('button',name='提问',exact=True).click()
            page.locator('.answer-result').wait_for()
            assert '不表示期间始终在场' in page.locator('.answer-result').inner_text()
            page.locator('.answer-result .reference').first.click()
            page.locator('.evidence-view img').first.wait_for()
            page.get_by_label('复核说明（必填）').fill('Synthetic browser test only: mark this one source uncertain.')
            page.get_by_role('button',name='标记无法确定',exact=True).click()
            page.get_by_text('人工已审查 1 / 9 条',exact=True).wait_for()
            state = httpx.get(f'{base}/api/videos/{vid}/review').json()
            assert sum(bool(e['human']) for e in state['entries']) == 1
            assert next(e for e in state['entries'] if e['human'])['original']['id'] == source_id
            after = httpx.get(f'{base}/api/videos/{vid}/events').json()
            assert len(after['events']) == len(original['events']) == 9
            page.reload()
            page.get_by_text('9 条记录',exact=True).wait_for()
            page.locator('.review-panel>summary').click()
            page.get_by_text('人工已审查 1 / 9 条',exact=True).wait_for()
            page.locator('#timeline').scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT/'evidence/grouping-mock-desktop.png'),animations='disabled')
            page.set_viewport_size({'width':320,'height':844})
            page.screenshot(path=str(ROOT/'evidence/grouping-mock-mobile.png'),animations='disabled')
            page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
            overflow = page.evaluate('''() => [...document.querySelectorAll('body *')].filter(node => {
                const r = node.getBoundingClientRect(); return r.right > innerWidth && r.width > 0;
            }).map(node => ({tag:node.tagName,cls:node.className,width:node.getBoundingClientRect().width,
                right:node.getBoundingClientRect().right,text:node.textContent?.slice(0,80)})).slice(0,12)''')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), {'overflow':overflow,
                'widths':page.evaluate('({scroll:document.documentElement.scrollWidth,viewport:innerWidth})')}
            assert not errors, errors
            browser.close()
        assert httpx.get(base+'/api/health').json()['usage']['requests'] == 0
        report = {'is_mock':True,'paid_model_calls':0,'raw_events':9,'initial_display_items':5,
                  'passed':['observation_folding','action_evidence_deduplication','expand_sources','source_review_navigation',
                            'filter_preserves_unknown_boundary','raw_view_toggle','grouped_qa_with_evidence',
                            'one_source_review_only','reload_review_persistence','mobile_320_no_overflow'],
                  'page_errors':errors,'artifacts':str(work)}
        (ROOT/'evidence/grouping-mock-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    main()

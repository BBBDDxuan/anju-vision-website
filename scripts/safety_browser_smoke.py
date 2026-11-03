"""One focused synthetic browser flow; no external API or real OS notifications."""
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
    work = ROOT/'data'/'browser-safety'/uuid4().hex
    work.mkdir(parents=True)
    base = 'http://127.0.0.1:8018'
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1', 8018)) != 0, 'Test port occupied'
    source = work/'SYNTHETIC_PERSON_FIXTURE_NOT_REAL_RECOGNITION.mp4'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 10, (640, 360))
    assert writer.isOpened()
    for _ in range(120):
        image = np.full((360, 640, 3), (40, 60, 40), np.uint8)
        cv2.putText(image, 'SYNTHETIC PERSON TEST ONLY', (20, 60), cv2.FONT_HERSHEY_SIMPLEX, .8, (230, 240, 220), 1)
        writer.write(image)
    writer.release()
    log = (work/'server.log').open('w', encoding='utf8')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'tests.safety_browser_server:create_test_app', '--factory',
        '--host', '127.0.0.1', '--port', '8018'], cwd=ROOT,
        env=dict(os.environ, SHIJI_BROWSER_TEST_DIR=str(work/'data'), PYTHONUTF8='1'), stdout=log, stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    errors = []
    try:
        for _ in range(100):
            try:
                if httpx.get(base+'/api/health', timeout=1).status_code == 200: break
            except httpx.RequestError: pass
            time.sleep(.1)
        else: raise RuntimeError('Test server did not start')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.add_init_script("""window.testNotifications=[];class MockNotification {
              static permission='default';static async requestPermission(){this.permission='granted';return 'granted';}
              constructor(title,options){window.testNotifications.push({title,options});}}
              Object.defineProperty(window,'Notification',{value:MockNotification,configurable:true});""")
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            assert '安居视界' in page.title()
            page.get_by_role('button', name='打开监测工作台', exact=True).click()
            page.locator('input[type=file]').first.set_input_files(str(source))
            page.get_by_role('button', name='开始 AI 分析', exact=True).wait_for()
            page.get_by_role('button', name='启用浏览器通知', exact=True).click()
            page.get_by_role('button', name='浏览器通知已启用', exact=True).wait_for()
            page.get_by_role('button', name='开始 AI 分析', exact=True).click()
            httpx.post(base+'/test/release').raise_for_status()
            page.locator('.safety-alert.urgent').wait_for()
            page.locator('.event.priority-event').wait_for()
            assert page.locator('.event.priority-event').evaluate("e => getComputedStyle(e,'::after').backgroundColor") == 'rgb(198, 75, 64)'
            assert '谨慎参考' in page.locator('.event.priority-event').inner_text()
            page.wait_for_function('() => window.testNotifications.length === 1')
            assert '历史录像' in page.evaluate('window.testNotifications[0].title')
            assert '模拟测试' in page.locator('.safety-alert').inner_text()
            page.get_by_role('button', name='查看视频证据 ↗', exact=True).click()
            page.locator('.safety-frames img').first.wait_for()
            assert page.locator('.safety-frames img').count() == 2
            page.get_by_role('button', name='标记已知悉', exact=True).click()
            page.locator('.safety-alert.acknowledged').wait_for()
            assert page.evaluate('window.testNotifications.length') == 1
            categories = page.get_by_role('navigation', name='事件分类', exact=True)
            categories.get_by_role('button', name='物体事件', exact=True).click()
            assert page.locator('.event').count() == 1
            assert '白色保温杯' in page.locator('.event').inner_text()
            assert '人物1' not in page.locator('.event').inner_text()
            categories.get_by_role('button', name='人物事件', exact=True).click()
            assert page.locator('.event').count() == 2
            assert '人物1（测试） 拿起白色保温杯' in page.locator('#timeline').inner_text()
            assert '被拿起' not in page.locator('#timeline').inner_text()
            assert page.locator('.event.priority-event').count() == 0
            page.get_by_role('button', name='重要事件', exact=True).click()
            assert page.locator('.safety-alert.urgent').count() == 1
            assert page.locator('.event').count() == 0
            page.locator('#safety').scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT/'evidence/safety-browser-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.locator('#safety').scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT/'evidence/safety-browser-mobile.png'))
            vid = page.locator('#video-select').input_value()
            report = httpx.get(base+f'/api/videos/{vid}/alerts').json()
            assert report['alerts'][0]['acknowledged'] and report['evidence'] and report['frames']
            assert httpx.get(base+'/api/health').json()['usage']['requests'] == 0
            assert not errors, errors
            browser.close()
        result = {'fixture': 'SYNTHETIC MOCK - NOT REAL PERSON RECOGNITION', 'paid_calls': 0,
            'passed': ['industrial_name', 'upload_analyze_person_alert', 'browser_notification_contract_once',
                       'historical_recording_label', 'red_inferred_event_keeps_uncertainty', 'evidence_frames', 'acknowledge_persisted',
                       'object_person_category_filters','person_general_important_filters','active_person_interaction_text','mobile_layout', 'no_js_errors']}
        (ROOT/'evidence/safety-browser.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps(result, ensure_ascii=False))
    finally:
        try: httpx.post(base+'/test/release', timeout=1)
        except httpx.RequestError: pass
        process.terminate()
        try: process.wait(10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        log.close()


if __name__ == '__main__': main()

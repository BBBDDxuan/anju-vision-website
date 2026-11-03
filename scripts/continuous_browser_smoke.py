"""Check production UI against an isolated synthetic server; zero paid API calls."""
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


def wait_until(predicate, timeout=30):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except httpx.RequestError:
            pass
        time.sleep(.1)
    raise AssertionError('Browser test timed out')


def main():
    work = ROOT/'data'/'browser-continuous'/uuid4().hex
    work.mkdir(parents=True)
    base = 'http://127.0.0.1:8017'
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1', 8017)) != 0, 'Test port is occupied'
    source = work/'SYNTHETIC_CONTINUOUS_ONLY.mp4'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 10, (640, 360))
    assert writer.isOpened()
    for _ in range(120):
        frame = np.full((360, 640, 3), (34, 59, 41), np.uint8)
        cv2.putText(frame, 'SYNTHETIC CONTINUOUS TEST ONLY', (15, 60), cv2.FONT_HERSHEY_SIMPLEX, .7, (230, 240, 210), 1)
        writer.write(frame)
    writer.release()
    log = (work/'server.log').open('w', encoding='utf8')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'tests.continuous_browser_server:create_test_app',
                                '--factory', '--host', '127.0.0.1', '--port', '8017'], cwd=ROOT,
                               env=dict(os.environ, SHIJI_BROWSER_TEST_DIR=str(work/'data'), PYTHONUTF8='1'),
                               stdout=log, stderr=log,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    errors, checks = [], []
    try:
        wait_until(lambda: httpx.get(base+'/api/health', timeout=1).status_code == 200)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base+'/workspace')
            page.locator('input[type=file]').first.set_input_files(str(source))
            page.get_by_role('button', name='开始 AI 分析', exact=True).wait_for()
            page.locator('.analysis-details summary').click()
            assert '批次自动接续' in page.locator('.analysis-details').inner_text()
            page.get_by_role('button', name='开始 AI 分析', exact=True).click()
            wait_until(lambda: httpx.get(base+'/test/state').json()['entered'])
            page.get_by_role('button', name='暂停分析', exact=True).click()
            page.get_by_role('button', name='正在保存并暂停…', exact=True).wait_for()
            assert page.get_by_role('button', name='正在保存并暂停…', exact=True).is_disabled()
            checks.append('pause request visibly waits for inflight result')
            httpx.post(base+'/test/release').raise_for_status()
            page.get_by_role('button', name='继续分析', exact=True).wait_for()
            assert '已手动暂停' in page.locator('.analysis-bar').inner_text()
            vid = page.locator('#video-select').input_value()
            first = httpx.get(base+'/api/videos/'+vid).json()['latest_job']
            assert first['completed_windows'] == 1 and first['calls'] == 1
            page.screenshot(path=str(ROOT/'evidence/continuous-paused-desktop.png'), full_page=True)
            page.reload()
            page.get_by_role('button', name='继续分析', exact=True).wait_for()
            checks.append('paused checkpoint survives page reload')
            page.get_by_role('button', name='继续分析', exact=True).click()
            page.wait_for_function("() => document.querySelector('.analysis-bar')?.innerText.includes('分析完成')")
            done = httpx.get(base+'/api/videos/'+vid).json()['latest_job']
            assert done['id'] == first['id'] and done['calls'] == 2
            assert httpx.get(base+'/test/state').json()['windows'] == [0, 6000]
            assert page.get_by_role('button', name='暂停分析', exact=True).count() == 0
            checks.append('resume reuses completed window and completes')
            # Start a fresh job and close the tab: automatic continuation must be server-owned.
            page.get_by_role('button', name='重新分析', exact=True).click()
            page.get_by_role('button', name='暂停分析', exact=True).wait_for()
            page.close()
            def completed():
                job = httpx.get(base+'/api/videos/'+vid).json()['latest_job']
                return job if job['status'] == 'completed' and job['id'] != done['id'] else None
            automatic = wait_until(completed)
            assert automatic['batch'] == 2 and automatic['calls'] == 2
            checks.append('automatic batches complete with browser tab closed')
            mobile = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True)
            mobile.goto(base+'/workspace?video='+vid)
            mobile.get_by_role('button', name='重新分析', exact=True).wait_for()
            assert mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
            mobile.screenshot(path=str(ROOT/'evidence/continuous-completed-mobile.png'), full_page=True)
            checks.append('mobile layout fits after automatic completion')
            assert not errors, errors
            checks.append('no browser JavaScript errors')
            browser.close()
        result = {'fixture': 'SYNTHETIC MOCK ONLY - NOT REAL MODEL VALIDATION', 'paid_calls': 0,
                  'checks': checks, 'automatic_job': automatic}
        (ROOT/'evidence/continuous-browser.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps({'passed': len(checks), 'paid_calls': 0, 'batch': automatic['batch']}, ensure_ascii=False))
    finally:
        try:
            httpx.post(base+'/test/release', timeout=1)
        except httpx.RequestError:
            pass
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


if __name__ == '__main__':
    main()

"""Isolated synthetic clip / mock vision end-to-end browser verification, zero paid calls."""
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

ROOT=Path(__file__).resolve().parents[1]


def main():
    base='http://127.0.0.1:8021';work=ROOT/'data/browser-static-filter'/uuid4().hex;work.mkdir(parents=True)
    with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1',8021))!=0,'Test port occupied'
    source=work/'SYNTHETIC_STATIC_FILTER_ONLY.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(320,180));assert writer.isOpened()
    image=np.full((180,320,3),60,np.uint8)
    cv2.putText(image,'MOCK STATIC FILTER TEST ONLY',(8,50),cv2.FONT_HERSHEY_SIMPLEX,.5,(200,200,200),1)
    for _ in range(120):writer.write(image)
    writer.release();log=(work/'server.log').open('w',encoding='utf8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','tests.activity_browser_server:create_test_app','--factory',
        '--host','127.0.0.1','--port','8021'],cwd=ROOT,env=dict(os.environ,SHIJI_BROWSER_TEST_DIR=str(work/'data'),PYTHONUTF8='1'),
        stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    errors=[]
    try:
        for _ in range(100):
            try:
                if httpx.get(base+'/api/health',timeout=1).status_code==200:break
            except httpx.RequestError:pass
            time.sleep(.1)
        else:raise RuntimeError('Test server did not start')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/workspace');page.get_by_label('上传 MP4',exact=True).set_input_files(str(source))
            page.get_by_role('button',name='开始 AI 分析',exact=True).wait_for()
            page.locator('.activity-plan').wait_for()
            page.get_by_role('button',name='开始 AI 分析',exact=True).click()
            page.wait_for_function('() => document.querySelector(".analysis-bar")?.innerText.includes("分析完成")')
            summary=page.locator('.activity-summary')
            assert '已跳过 4 个窗口' in summary.inner_text() and '模型分析 2 个窗口' in summary.inner_text()
            assert page.locator('.coverage-track .static-screened').count()==2
            assert '灰色：静止过滤' in page.locator('.coverage').inner_text()
            summary.locator('summary').click()
            assert '未调用视觉模型' in summary.inner_text()
            page.get_by_label('你想找回什么线索？',exact=True).fill('视频发生了什么？')
            page.get_by_role('button',name='提问',exact=True).click();page.locator('.answer-result').wait_for()
            assert '未调用视觉模型' in page.locator('.answer-result').inner_text()
            vid=page.locator('#video-select').input_value()
            job=httpx.get(f'{base}/api/videos/{vid}/events').json()['job']
            assert job['calls']==2 and job['total_windows']==6 and job['completed_windows']==6
            page.reload();page.locator('.activity-summary').wait_for()
            page.locator('.activity-summary').scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT/'evidence/static-filter-browser.png'))
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            browser.close()
        assert httpx.get(base+'/api/health').json()['usage']['requests']==0
        report={'is_mock':True,'paid_model_calls':0,'windows':6,'mock_model_calls':2,'static_skipped_windows':4,
                'passed':['upload_and_screen','periodic_review','honest_coverage','qa_gap_disclosure','reload_persistence','mobile_layout'],
                'page_errors':errors}
        (ROOT/'evidence/static-filter-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close()


if __name__=='__main__':main()

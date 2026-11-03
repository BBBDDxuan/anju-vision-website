"""Chrome upload (>60s), pause/resume, persistence and late evidence test; explicitly MOCK vision."""
from datetime import datetime, timezone
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
    work=Path('D:/MemoryOSHomePrep/long-browser')/uuid4().hex[:8]
    work.mkdir(parents=True)
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',5173))==0:
            raise RuntimeError('Port5173 is busy; leave existing service untouched')
    source=work/'SYNTHETIC_LONG_TEST_NOT_REAL_AI.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),5,(320,180))
    assert writer.isOpened()
    for i in range(360):
        image=np.full((180,320,3),(40,70,50),dtype=np.uint8)
        cv2.putText(image,f'SYNTHETIC TEST {i/5:.1f}s',(12,75),cv2.FONT_HERSHEY_SIMPLEX,.5,(220,240,230),1)
        writer.write(image)
    writer.release()
    env=dict(os.environ,SHIJI_LONG_BROWSER_DIR=str(work/'data'),PYTHONIOENCODING='utf-8')
    log=(work/'server.log').open('w',encoding='utf8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','tests.long_browser_server:create_test_app','--factory','--host','127.0.0.1','--port','5173'],cwd=ROOT,env=env,stdout=log,stderr=log)
    errors=[]
    try:
        for _ in range(100):
            try:
                if httpx.get('http://127.0.0.1:5173/api/health').status_code==200:break
            except httpx.RequestError:pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:5173')
            page.get_by_text('测试模拟模式',exact=False).wait_for()
            page.get_by_text('最长60分钟',exact=False).wait_for()
            page.get_by_label('上传 MP4').set_input_files(str(source))
            page.get_by_role('button',name='开始 AI 分析',exact=True).wait_for(timeout=30000)
            page.get_by_role('button',name='开始 AI 分析',exact=True).click()
            page.get_by_role('button',name='继续分析',exact=True).wait_for(timeout=30000)
            page.get_by_text('已保存2/12个窗口',exact=False).wait_for()
            page.reload()
            page.locator('#video-select').select_option(index=1)
            page.get_by_role('button',name='继续分析',exact=True).wait_for()
            for done in [4,6,8,10,12]:
                page.get_by_role('button',name='继续分析',exact=True).click()
                if done<12:
                    page.get_by_text(f'已保存{done}/12个窗口',exact=False).wait_for(timeout=30000)
                    page.get_by_role('button',name='继续分析',exact=True).wait_for()
                else:
                    page.get_by_role('button',name='重新分析',exact=True).wait_for(timeout=30000)
            page.get_by_text('12 条记录',exact=True).wait_for()
            page.get_by_label('你想找回什么线索？').fill('测试物品（非AI识别）最后在哪里出现？')
            page.get_by_role('button',name='提问',exact=True).click()
            page.locator('.answer-result .reference').first.wait_for()
            page.locator('.answer-result .reference').first.click()
            page.get_by_text('已定位至 01:11.0',exact=False).wait_for()
            assert abs(page.locator('video').evaluate('v => v.currentTime')-71)<.25
            page.screenshot(path=str(ROOT/'evidence/long-ui-mock.png'),full_page=True)
            browser.close()
        assert not errors,errors
        result={'status':'PASS','checked_at_utc':datetime.now(timezone.utc).isoformat(),'input_seconds':72,'vision':'EXPLICIT_TEST_MOCK_NOT_REAL_AI','paid_model_calls':0,
                'checks':['upload_over_60_seconds','12_windows_across_6_batches','pause_progress_not_success','reload_resume','no_duplicate_events','answer_late_evidence','seek_71_seconds'],
                'page_errors':errors}
        (ROOT/'evidence/long-browser-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(result,ensure_ascii=False))
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close()


if __name__=='__main__':main()

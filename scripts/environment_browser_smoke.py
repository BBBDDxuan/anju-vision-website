"""Focused synthetic flow; mock model and Notification, no real detection or paid requests."""
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
    base='http://127.0.0.1:8019';work=ROOT/'data/browser-environment'/uuid4().hex;work.mkdir(parents=True)
    with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1',8019))!=0,'Test port occupied'
    source=work/'SYNTHETIC_ENVIRONMENT_TEST_NOT_REAL_FIRE.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(640,360));assert writer.isOpened()
    for _ in range(120):
        image=np.full((360,640,3),(45,60,40),np.uint8)
        cv2.putText(image,'MOCK ONLY - NOT REAL FIRE DETECTION',(12,60),cv2.FONT_HERSHEY_SIMPLEX,.7,(230,240,220),1)
        writer.write(image)
    writer.release();log=(work/'server.log').open('w',encoding='utf8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','tests.environment_browser_server:create_test_app','--factory',
        '--host','127.0.0.1','--port','8019'],cwd=ROOT,env=dict(os.environ,SHIJI_BROWSER_TEST_DIR=str(work/'data'),PYTHONUTF8='1'),
        stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    errors=[]
    try:
        for _ in range(100):
            try:
                if httpx.get(base+'/api/health',timeout=1).status_code==200: break
            except httpx.RequestError: pass
            time.sleep(.1)
        else: raise RuntimeError('Test server did not start')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.add_init_script("""window.testNotifications=[];class MockNotification{
              static permission='default';static async requestPermission(){this.permission='granted';return 'granted';}
              constructor(title,options){window.testNotifications.push({title,options});}}
              Object.defineProperty(window,'Notification',{value:MockNotification,configurable:true});""")
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/workspace');page.locator('input[type=file]').first.set_input_files(str(source))
            page.get_by_role('button',name='开始 AI 分析',exact=True).wait_for()
            page.get_by_role('button',name='启用浏览器通知',exact=True).click()
            page.get_by_role('button',name='浏览器通知已启用',exact=True).wait_for()
            page.get_by_role('button',name='开始 AI 分析',exact=True).click();httpx.post(base+'/test/release').raise_for_status()
            page.wait_for_function("() => document.querySelector('.analysis-bar')?.innerText.includes('分析完成')")
            page.wait_for_function('() => document.querySelectorAll(".safety-alert.urgent").length === 3')
            page.wait_for_function('() => window.testNotifications.length === 3')
            categories=page.get_by_role('navigation',name='事件分类',exact=True)
            categories.get_by_role('button',name='物体事件',exact=True).click();assert page.locator('.event').count()==0
            categories.get_by_role('button',name='人物事件',exact=True).click()
            page.get_by_role('button',name='重要事件',exact=True).click();assert page.locator('.safety-alert').count()==0
            categories.get_by_role('button',name='环境危险事件',exact=True).click()
            assert page.locator('.safety-alert.urgent').count()==3
            smoke=page.locator('.safety-alert').filter(has_text='疑似烟雾')
            assert '水蒸气' in smoke.inner_text() and '推测/规则提醒' in smoke.inner_text()
            smoke.get_by_role('button',name='查看视频证据 ↗',exact=True).click()
            assert smoke.locator('img').count()==2
            smoke.get_by_role('button',name='标记已知悉',exact=True).click()
            page.locator('.safety-alert.acknowledged').wait_for()
            page.get_by_label('你想找回什么线索？',exact=True).fill('视频有没有烟雾？')
            page.get_by_role('button',name='提问',exact=True).click();page.locator('.answer-result .inference').first.wait_for()
            assert '烟雾' in page.locator('.answer-result').inner_text()
            page.locator('#safety').scroll_into_view_if_needed();page.screenshot(path=str(ROOT/'evidence/environment-browser-desktop.png'))
            page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            vid=page.locator('#video-select').input_value();report=httpx.get(base+f'/api/videos/{vid}/alerts').json()
            assert len(report['alerts'])==3 and all(a['category']=='environment' for a in report['alerts'])
            assert httpx.get(base+'/api/health').json()['usage']['requests']==0 and not errors
            browser.close()
        result={'fixture':'MOCK ONLY - NOT REAL HAZARD DETECTION','paid_calls':0,'passed':[
          'three_red_environment_types','person_object_categories_exclude_hazards','inference_and_steam_reason_retained',
          'notification_deduplicated','evidence_navigation','acknowledge_persisted','global_environment_qa','mobile_layout','no_js_errors']}
        (ROOT/'evidence/environment-browser.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(result,ensure_ascii=False))
    finally:
        try: httpx.post(base+'/test/release',timeout=1)
        except httpx.RequestError: pass
        process.terminate()
        try: process.wait(10)
        except subprocess.TimeoutExpired: process.kill();process.wait()
        log.close()


if __name__=='__main__': main()

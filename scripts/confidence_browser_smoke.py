"""Test confidence-only UI against synthetic video and explicit mock model, with zero paid calls."""
import json
import os
from pathlib import Path
import re
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
    work=Path('D:/MemoryOSHomePrep/confidence-browser')/uuid4().hex;work.mkdir(parents=True)
    base='http://127.0.0.1:8016'
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',8016))==0:raise RuntimeError('Test port 8016 is occupied')
    source=work/'SYNTHETIC_CONFIDENCE_TEST_ONLY.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(640,360));assert writer.isOpened()
    for _ in range(120):
        im=np.full((360,640,3),(34,59,41),np.uint8)
        cv2.putText(im,'SYNTHETIC CONFIDENCE TEST ONLY',(15,60),cv2.FONT_HERSHEY_SIMPLEX,.7,(230,240,210),1);writer.write(im)
    writer.release()
    env=dict(os.environ,SHIJI_BROWSER_TEST_DIR=str(work/'data'),PYTHONUTF8='1')
    log=(work/'server.log').open('w',encoding='utf8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','tests.confidence_browser_server:create_test_app','--factory',
                              '--host','127.0.0.1','--port','8016'],cwd=ROOT,env=env,stdout=log,stderr=log,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    errors=[];network=[]
    try:
        for _ in range(100):
            try:
                if httpx.get(base+'/api/health',timeout=1).status_code==200:break
            except httpx.RequestError:pass
            time.sleep(.1)
        else:raise RuntimeError('Test server not ready')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1600,'height':1100})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:network.append(request.url))
            page.goto(base+'/workspace')
            page.get_by_label('上传 MP4').set_input_files(str(source))
            page.get_by_role('button',name='开始 AI 分析',exact=True).click()
            page.get_by_text('7 条线索',exact=True).wait_for()
            vid=page.locator('#video-select').input_value()
            raw=httpx.get(f'{base}/api/videos/{vid}/events').json()
            assert len(raw['events'])==11
            assert page.locator('.event').count()==6
            assert page.locator('.event.confidence-high').count()==3
            assert page.locator('.event.confidence-medium').count()==3
            assert page.locator('.review-panel').count()==0
            assert page.get_by_role('button',name=re.compile('人工|复核|核对此条')).count()==0
            assert 'TEST_LOW_ONLY' not in page.locator('main').inner_text()
            group=page.locator('.event-group').first;group.locator('summary').click()
            assert group.locator('.event-source').count()==2
            group.locator('.reference').first.click();page.locator('.evidence-view img').first.wait_for()
            page.get_by_role('button',name='展开全部可见事件',exact=True).click()
            assert page.locator('.event').count()==7
            assert page.locator('.event.confidence-high').count()==4
            assert page.locator('.event.confidence-medium').count()==3
            page.locator('.event-filters button').filter(has_text='较高可信').click()
            assert page.locator('.event').count()==4
            page.locator('.event-filters button').filter(has_text='谨慎参考').click()
            assert page.locator('.event').count()==3
            assert '可能：' in page.locator('.event').filter(has_text='移动到').inner_text()
            page.locator('.event-filters button').filter(has_text='全部').click()
            page.get_by_role('button',name='折叠重复记录',exact=True).click()
            page.get_by_label('你想找回什么线索？',exact=True).fill('测试钥匙最后在哪里出现？')
            page.get_by_role('button',name='提问',exact=True).click();page.locator('.answer-result').wait_for()
            assert page.locator('.answer-result .claim[data-confidence]').count()==0
            assert '当前没有足够可信的证据' in page.locator('.answer-result').inner_text()
            page.get_by_label('你想找回什么线索？',exact=True).fill('测试钥匙的经过是什么？')
            page.get_by_role('button',name='提问',exact=True).click()
            page.wait_for_function('() => document.querySelectorAll(".answer-result .claim[data-confidence]").length > 0')
            assert 'TEST_LOW_ONLY' not in page.locator('.answer-result').inner_text()
            assert '复核' not in page.locator('.answer-result').inner_text()
            shown_refs=page.locator('.answer-result .reference').all_inner_texts()
            assert all('00:11.0' not in ref and '00:03.0' not in ref for ref in shown_refs)
            assert page.locator('.answer-result .inference.confidence-medium').count()==1
            page.get_by_role('button',name='LifeReplay',exact=True).click()
            item=next(e['id'] for e in raw['entities'] if e['label']=='测试钥匙')
            page.locator('#entity-select').select_option(item)
            page.wait_for_function('() => document.querySelectorAll(".replay-entry").length === 7')
            assert page.locator('.replay-entry.confidence-high').count()==4
            assert page.locator('.replay-entry.confidence-medium').count()==3
            assert 'TEST_LOW_ONLY' not in page.locator('#entity-select').inner_text()
            page.get_by_role('button',name='全部事件',exact=True).click()
            page.locator('#timeline').scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT/'evidence/confidence-mock-desktop.png'),animations='disabled')
            for width in [390,320]:
                page.set_viewport_size({'width':width,'height':844})
                page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(ROOT/'evidence/confidence-mock-mobile.png'),animations='disabled')
            page.reload();page.get_by_text('7 条线索',exact=True).wait_for()
            assert page.locator('.review-panel').count()==0
            assert not errors,errors
            browser.close()
        after=httpx.get(f'{base}/api/videos/{vid}/events').json()
        assert raw['events']==after['events'] and raw['evidence']==after['evidence']
        assert httpx.get(base+'/api/health').json()['usage']['requests']==0
        assert not any('/review' in url or '/regions' in url for url in network)
        report={'is_mock':True,'paid_model_calls':0,'raw_events':11,'visible_events':7,'green_events':4,'yellow_events':3,
                'passed':['manual_options_removed','green_yellow_tiers','missing_score_stays_yellow','high_score_inference_stays_yellow',
                          'hidden_not_revealed_by_expand','confidence_filters','evidence_navigation','hidden_latest_no_false_fallback',
                          'history_drops_hidden_refs','replay_filters_hidden','hidden_only_objects_not_suggested',
                          'mobile_390_320_no_overflow','reload','stored_events_and_evidence_unchanged'],
                'page_errors':errors,'artifacts':str(work)}
        (ROOT/'evidence/confidence-mock-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close()


if __name__=='__main__':main()

"""Landing/navigation and destructive actions are tested only with synthetic local uploads."""
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

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = Path('D:/MemoryOSHomePrep/home-delete-browser')/uuid4().hex
    work.mkdir(parents=True)
    base = 'http://127.0.0.1:8014'
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',8014)) == 0: raise RuntimeError('Test port 8014 is occupied')
    source = work/'SYNTHETIC_DELETE_TEST_ONLY.mp4'
    writer = cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(640,360))
    assert writer.isOpened()
    for _ in range(120):
        frame = np.full((360,640,3),(34,59,41),np.uint8)
        cv2.putText(frame,'SYNTHETIC DELETION TEST ONLY',(15,60),cv2.FONT_HERSHEY_SIMPLEX,.8,(230,240,210),1)
        writer.write(frame)
    writer.release()
    env = dict(os.environ,SHIJI_BROWSER_TEST_DIR=str(work/'data'),PYTHONUTF8='1')
    log = (work/'server.log').open('w',encoding='utf8')
    process = subprocess.Popen([sys.executable,'-m','uvicorn','tests.grouping_browser_server:create_test_app',
                                '--factory','--host','127.0.0.1','--port','8014'],cwd=ROOT,env=env,stdout=log,stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    errors = []
    try:
        for _ in range(100):
            try:
                if httpx.get(base+'/api/health',timeout=1).status_code == 200: break
            except httpx.RequestError: pass
            time.sleep(.1)
        else: raise RuntimeError('Test server failed to start')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome',headless=True)
            page = browser.new_page(viewport={'width':1600,'height':1100})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base)
            page.locator('.landing-page').wait_for()
            assert page.get_by_label('上传 MP4').count() == 0
            page.screenshot(path=str(ROOT/'evidence/home-mock-desktop.png'),full_page=True,animations='disabled')
            page.get_by_role('button',name='打开记忆工作台',exact=True).click()
            page.wait_for_url('**/workspace')
            page.get_by_label('上传 MP4').wait_for()
            page.go_back();page.locator('.landing-page').wait_for()
            page.go_forward();page.get_by_label('上传 MP4').wait_for()
            page.reload();page.get_by_label('上传 MP4').wait_for()
            page.get_by_label('上传 MP4').set_input_files(str(source))
            page.get_by_role('button',name='开始 AI 分析',exact=True).click()
            page.get_by_text('8 条线索',exact=True).wait_for()
            first = page.locator('#video-select').input_value()
            first_job = httpx.get(f'{base}/api/videos/{first}').json()['latest_job']['id']
            assert page.locator('.review-panel').count()==0
            # Seed a historical overlay through the isolated test API only. The
            # current UI intentionally offers no manual review controls.
            state=httpx.get(f'{base}/api/videos/{first}/review').json()
            seeded=httpx.post(f'{base}/api/videos/{first}/review/{state["entries"][0]["original"]["id"]}/human',json={
                'run_id':first_job,'expected_revision':state['revision'],'decision':'confirm',
                'note':'Synthetic historical overlay: delete with this test video only.'})
            assert seeded.status_code==200
            page.goto(base+'/?video='+first)
            page.get_by_text('8 条线索',exact=True).wait_for()
            page.wait_for_url('**/workspace?video='+first)
            page.get_by_role('button',name='删除视频',exact=True).click()
            dialog = page.get_by_role('dialog',name='删除这段录像？')
            dialog.wait_for();assert source.name in dialog.inner_text()
            page.screenshot(path=str(ROOT/'evidence/delete-confirm-mock.png'),animations='disabled')
            page.keyboard.press('Escape');dialog.wait_for(state='detached')
            assert httpx.get(f'{base}/api/videos/{first}').status_code == 200
            page.get_by_role('button',name='删除视频',exact=True).click()
            page.get_by_role('button',name='取消',exact=True).click()
            assert httpx.get(f'{base}/api/videos/{first}').status_code == 200
            second_file = {'name':'SYNTHETIC_KEEP_OTHER.mp4','mimeType':'video/mp4','buffer':source.read_bytes()}
            page.get_by_label('上传 MP4').set_input_files(second_file)
            page.wait_for_function('(id) => document.querySelector("#video-select").value && document.querySelector("#video-select").value !== id',arg=first)
            second = page.locator('#video-select').input_value()
            assert page.locator('video').count() == 1, 'Switching recordings must replace the previous player'
            page.get_by_role('button',name=f'删除录像 {source.name}',exact=True).click()
            page.get_by_role('button',name='确认删除',exact=True).click()
            page.get_by_role('dialog').wait_for(state='detached')
            assert page.locator('#video-select').input_value() == second
            assert httpx.get(f'{base}/api/videos/{first}').status_code == 404
            assert httpx.get(f'{base}/api/jobs/{first_job}').status_code == 404
            assert not (work/'data/videos'/first).exists() and not (work/'data/runs'/first_job).exists()
            assert httpx.get(f'{base}/api/videos/{second}').status_code == 200
            page.get_by_role('button',name='删除视频',exact=True).click()
            page.get_by_role('button',name='确认删除',exact=True).click()
            page.get_by_role('dialog').wait_for(state='detached')
            page.wait_for_function('() => document.querySelector("#video-select").value === ""')
            page.locator('video').wait_for(state='detached')
            assert httpx.get(base+'/api/videos').json()['videos'] == []
            page.reload();page.get_by_label('上传 MP4').wait_for()
            assert page.locator('#video-select').input_value() == ''
            page.get_by_role('link',name=re.compile('产品介绍')).click()
            page.locator('.landing-page').wait_for()
            for width in [390,320]:
                page.set_viewport_size({'width':width,'height':844})
                page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(ROOT/'evidence/home-mock-mobile.png'),full_page=True,animations='disabled')
            page.get_by_role('button',name='打开导航',exact=True).click()
            page.get_by_role('link',name=re.compile('记忆工作台')).click()
            page.get_by_label('上传 MP4').wait_for()
            page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors,errors
            browser.close()
        assert httpx.get(base+'/api/health').json()['usage']['requests'] == 0
        report = {'is_mock':True,'paid_model_calls':0,'actual_user_videos_deleted':0,
                  'passed':['landing_default','workspace_entry','back_forward_reload','legacy_video_deep_link',
                            'delete_confirmation','escape_and_cancel_preserve_video','delete_unselected_video',
                            'other_video_preserved','owned_files_removed','delete_selected_clears_player',
                            'delete_persists_after_reload','mobile_home_390_320','mobile_workspace_navigation'],
                  'page_errors':errors,'artifacts':str(work)}
        (ROOT/'evidence/home-delete-mock-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        log.close()


if __name__ == '__main__': main()

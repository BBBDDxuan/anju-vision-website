"""Smoke-test the exported cloud bundle locally; no new model calls."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    export = json.loads((ROOT/'data/modelscope-export.json').read_text(encoding='utf8'))
    bundle = Path(export['directory'])
    shutil.copytree(ROOT/'frontend/dist', bundle/'frontend/dist', dirs_exist_ok=True)
    out = ROOT/'data/validation/modelscope-bundle'
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONUTF8':'1', 'CLOUD_MODE':'true', 'CLOUD_COOKIE_SECURE':'false',
           'APP_ALLOWED_HOSTS':'127.0.0.1,localhost', 'DATA_DIR':str(out/'runtime'),
           'VLM_API_KEY':'', 'DASHSCOPE_API_KEY':'', 'VLM_ENABLE_NETWORK':'false',
           'MAX_MODEL_CALLS_PER_DAY':'100',
           'MAX_VIDEO_SECONDS':'0', 'MAX_UPLOAD_MB':'0', 'CLOUD_STORAGE_MB':'0', 'MAX_CONCURRENCY':'1',
           'NO_PROXY':'127.0.0.1,localhost', 'no_proxy':'127.0.0.1,localhost'}
    imported = subprocess.run([sys.executable, 'scripts/import_demo.py', '--all'], cwd=bundle, env=env,
                              capture_output=True, text=True, check=True)
    stdout = (out/'server.stdout.log').open('w', encoding='utf8')
    stderr = (out/'server.stderr.log').open('w', encoding='utf8')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:create_app', '--factory',
        '--host', '127.0.0.1', '--port', '8021', '--no-access-log'], cwd=bundle, env=env,
        stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        with httpx.Client(base_url='http://127.0.0.1:8021', trust_env=False, timeout=15) as client:
            deadline=time.monotonic()+25
            while True:
                try:
                    health=client.get('/api/health').json()
                    break
                except httpx.ConnectError:
                    if time.monotonic()>deadline or process.poll() is not None: raise
                    time.sleep(.2)
            assert health['cloud_mode'] and not health['analysis_ready']
            assert health['limits']['max_video_seconds'] is None and health['limits']['max_upload_mb'] is None
            assert health['limits']['max_daily_calls']==100
            demos=client.get('/api/demos').json()['demos']
            assert len(demos)==4 and {d['case_type'] for d in demos} == {'fire','day_fall','night_fall','long_video'}
            assert all(d['record_count']>0 for d in demos)
            assert all(d['priority_count']>0 for d in demos if d['case_type']!='long_video')
            vid=next(d['id'] for d in demos if d['case_type']=='day_fall')
            for demo in demos:
                assert client.get(f"/media/videos/{demo['id']}",headers={'Range':'bytes=0-99'}).status_code==206
            assert client.delete(f'/api/videos/{vid}').status_code==403
            response=client.post(f'/api/videos/{vid}/query',json={'question':'视频中什么时候有人跌倒？'})
            assert response.status_code==200 and response.json()['evidence']
            assert client.get(f'/media/videos/{vid}',headers={'Range':'bytes=0-99'}).status_code==206
            errors=[]
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto('http://127.0.0.1:8021/')
                page.locator('.demo-card').first.wait_for()
                assert page.locator('.demo-card').count()==4
                page.wait_for_function('() => [...document.querySelectorAll(".demo-poster img")].every(img => img.complete && img.naturalWidth > 0)')
                page.screenshot(path=str(out/'gallery-desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                page.screenshot(path=str(out/'gallery-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator('.demo-card').filter(has_text='白天跌倒').get_by_role('button',name='打开录像与证据 ↗',exact=True).click()
                page.locator('.safety-alert').first.wait_for()
                assert page.get_by_role('button',name='重新分析此案例',exact=True).is_disabled()  # Network deliberately disabled in this smoke test.
                assert page.get_by_role('button',name='删除视频',exact=True).is_disabled()
                assert '不设大小与时长上限' in page.locator('.upload-zone').inner_text()
                page.locator('#question').fill('视频中什么时候有人跌倒？')
                page.get_by_role('button',name='提问',exact=True).click()
                page.locator('.answer-result .reference').first.wait_for()
                page.screenshot(path=str(out/'desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                page.screenshot(path=str(out/'mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                long_id=next(d['id'] for d in demos if d['case_type']=='long_video')
                page.goto('http://127.0.0.1:8021/workspace?video='+long_id)
                page.wait_for_function('() => document.querySelector("video")?.duration > 390')
                page.wait_for_function('() => document.querySelector(".event-categories > button[aria-pressed=true]")?.textContent === "物体事件"')
                page.locator('#timeline .event').first.wait_for()
                page.locator('video').evaluate('v => {v.currentTime=300;v.pause()}')
                page.wait_for_function('() => Math.abs(document.querySelector("video").currentTime-300)<.3 && document.querySelector("video").readyState>=2')
                page.screenshot(path=str(out/'long-demo.png'),full_page=True)
                fire_id=next(d['id'] for d in demos if d['case_type']=='fire')
                fire_answer=client.post(f'/api/videos/{fire_id}/query',json={'question':'视频中有哪些值得关注的情况？'}).json()
                assert len(fire_answer['observations'])+len(fire_answer['inferences'])==3
                padded=out/'chunk-transfer-test-only.mp4'
                with (ROOT/'video/火光_可上传.mp4').open('rb') as source,padded.open('wb') as target:
                    shutil.copyfileobj(source,target)
                    padding=51*1024**2
                    target.write(struct.pack('>I4s',padding,b'free'))
                    target.seek(padding-9,1);target.write(b'\0')
                with page.expect_response(lambda r:'/api/uploads/' in r.url and r.url.endswith('/complete')) as uploaded:
                    page.get_by_label('上传 MP4',exact=True).set_input_files(str(padded))
                assert uploaded.value.status==201
                uploaded_id=uploaded.value.json()['video_id']
                assert page.evaluate('async id=>(await fetch(`/api/videos/${id}/delete`,{method:"POST"})).status',uploaded_id)==200
                browser.close()
            assert not errors,errors
            assert client.get('/api/health').json()['usage']['requests']==0
            result={'bundle':str(bundle),'verified':True,'new_model_calls':0,'public_demo_qa':True,
                    'evidence_video_range':True,'browser_interactions':True,'mobile_overflow':False,
                    'public_demo_count':4,'chunk_upload_over_50mib':True,'docker_build_verified':False,'import':json.loads(imported.stdout)}
            (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps(result,ensure_ascii=False))
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        stdout.close();stderr.close()


if __name__=='__main__':
    main()

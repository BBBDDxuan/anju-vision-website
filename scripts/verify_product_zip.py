"""Extract and launch the delivered ZIP with no key/network; never use production data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from uuid import uuid4

from dotenv import dotenv_values
import httpx
from playwright.sync_api import sync_playwright

from backend.config import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private',action='store_true')
    args=parser.parse_args()
    os.environ['NO_PROXY']=os.environ['no_proxy']='127.0.0.1,localhost'
    build_path = ROOT/'evidence/product-package-build.json'
    all_build = json.loads(build_path.read_text(encoding='utf8'))
    build = all_build['private'] if args.private else all_build
    archive = Path(build['archive'])
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==build['archive_sha256']
    work = ROOT/'data/validation'/('产品解压检查 '+archive.stem.rsplit('_',1)[-1]+' '+uuid4().hex[:6])
    work.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            path = (work/name).resolve()
            assert path.is_relative_to(work.resolve()) and not Path(name).is_absolute()
        z.extractall(work)
    package = next(p for p in work.iterdir() if p.is_dir())
    manifest = json.loads((package/'MANIFEST_SHA256.json').read_text(encoding='utf8'))
    for relative,expected in manifest.items():
        assert hashlib.sha256((package/relative).read_bytes()).hexdigest()==expected
    env = dict(os.environ)
    private = dotenv_values(ROOT/'.env')
    for key in set(private) | set(dotenv_values(package/'.env.example')) | {'DASHSCOPE_API_KEY'}:
        env.pop(key,None)
    env.update(PYTHONUTF8='1',VLM_API_KEY='',DASHSCOPE_API_KEY='',VLM_ENABLE_NETWORK='false',
               DATA_DIR=str(package/'data'))
    for key in ('FFMPEG_PATH','FFPROBE_PATH'):
        if private.get(key):
            env[key]=private[key]
    if args.private:
        expected=private.get('VLM_API_KEY') or private.get('DASHSCOPE_API_KEY')
        assert dotenv_values(package/'.env').get('VLM_API_KEY')==expected
    else:
        shutil.copy2(package/'.env.example',package/'.env')
    port = 8019
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1',port))!=0,'Package test port is occupied'
    base = f'http://127.0.0.1:{port}'
    errors = []
    with (work/'startup.log').open('w',encoding='utf8') as log:
        process = subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                                    str(package/'start.ps1'),'-Python',sys.executable,'-Port',str(port)],
                                   cwd=package,env=env,stdout=log,stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline=time.monotonic()+60
            while True:
                try:
                    health=httpx.get(base+'/api/health',timeout=2).json()
                    break
                except (httpx.HTTPError,ValueError):
                    if process.poll() is not None or time.monotonic()>deadline:
                        raise RuntimeError('Extracted launcher did not start; see isolated startup.log')
                    time.sleep(.2)
            assert health['usage']['requests']==0 and not health['analysis_ready']
            assert health['limits']['max_daily_calls']==60
            videos=httpx.get(base+'/api/videos').json()['videos']
            assert len(videos)==1 and 'CAUCAFall S02' in videos[0]['name']
            vid=videos[0]['id']
            memory=httpx.get(f'{base}/api/videos/{vid}/events').json()
            assert memory['job']['imported_recording'] and memory['fixture_kind']=='model_analysis' and not memory['is_mock']
            answer=httpx.post(f'{base}/api/videos/{vid}/query',json={'question':'人物1什么时候跌倒？'}).json()
            assert len(answer['observations'])==1 and answer['evidence']
            assert httpx.get(f'{base}/media/videos/{vid}',headers={'Range':'bytes=0-99'}).status_code==206
            assert httpx.get(base+memory['frames'][0]['url']).status_code==200
            again=subprocess.run([sys.executable,'scripts/import_demo.py'],cwd=package,env=env,capture_output=True,timeout=30)
            assert again.returncode==0 and json.loads(again.stdout)['status']=='ALREADY_PRESENT'
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1050})
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base)
                page.get_by_role('button',name='打开监测工作台',exact=True).click()
                page.locator('#video-select').select_option(vid)
                alert=page.locator('.safety-alert').filter(has_text='疑似跌倒')
                alert.wait_for()
                assert alert.count()==1
                assert page.get_by_role('button',name='重新分析',exact=True).is_disabled()
                alert.get_by_role('button',name='查看视频证据 ↗',exact=True).click()
                page.wait_for_function('() => Math.abs(document.querySelector("video").currentTime-2)<.3')
                page.get_by_label('你想找回什么线索？',exact=True).fill('人物1什么时候跌倒？')
                page.get_by_role('button',name='提问',exact=True).click()
                page.locator('.claim').filter(has_text='出现疑似跌倒过程').wait_for()
                page.screenshot(path=str(ROOT/'evidence/product-package-offline.png'))
                assert not errors,errors
                browser.close()
            with (package/'video/火光_可上传.mp4').open('rb') as file:
                uploaded=httpx.post(base+'/api/videos',files={'file':('fire-compatible.mp4',file,'video/mp4')},timeout=60)
            uploaded.raise_for_status()
            fire_id=uploaded.json()['video_id']
            assert httpx.get(f'{base}/media/videos/{fire_id}',headers={'Range':'bytes=0-99'}).status_code==206
            assert httpx.post(f'{base}/api/videos/{fire_id}/analyze').status_code==503
            assert httpx.get(base+'/api/health').json()['usage']['requests']==0
        finally:
            if process.poll() is None:
                subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=20)
            process.wait(timeout=20)
    report={'archive':str(archive),'extracted_path':str(package),'files_verified':len(manifest),
            'passed':['zip_extraction_hashes','powershell_launcher_unicode_path','offline_real_demo_import',
                      'idempotent_import','homepage_to_workspace','saved_event_timeline_and_alert',
                      'offline_evidence_qa','video_evidence_seek','frame_endpoint','video_range_playback',
                      'fire_video_upload','unconfigured_model_blocked','daily_limit_60'],
            'model_calls':0,'page_errors':errors,
            'limitations':'Used the existing Python environment and media tools; did not perform a fresh internet dependency installation.'}
    report['contains_api_key']=args.private
    (ROOT/('evidence/product-package-private-validation.json' if args.private else 'evidence/product-package-validation.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    build['extracted_startup_check']='passed'
    build_path.write_text(json.dumps(all_build,ensure_ascii=False,indent=2),encoding='utf8')
    sidecar=archive.with_suffix('.validation.json')
    sidecar.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()

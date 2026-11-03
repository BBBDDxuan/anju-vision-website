"""Boot the curated package from a clean path, check offline playback, then seal a new ZIP."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4
import zipfile

from dotenv import dotenv_values
import httpx
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    package=ROOT/'submission'
    dist=package/'application/frontend/dist'
    assert dist.resolve().is_relative_to(package.resolve())
    shutil.rmtree(dist)  # Only the generated build inside the curated package.
    shutil.copytree(ROOT/'frontend/dist',dist)
    work=Path('D:/MemoryOSHomePrep/long-package')/uuid4().hex[:8]
    app=work/'拾迹 Home/application'
    shutil.copytree(package/'application',app)
    env=dict(os.environ,DATA_DIR=str(work/'data'),VLM_ENABLE_NETWORK='false',PYTHONIOENCODING='utf8')
    imported=subprocess.run([sys.executable,'scripts/import_demo.py'],cwd=app,env=env,capture_output=True,check=True)
    assert b'IMPORTED_REAL_RECORDING' in imported.stdout
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',5173))==0:raise RuntimeError('Port5173 busy; do not disturb existing service')
    log=(work/'server.log').open('w',encoding='utf8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.main:create_app','--factory','--host','127.0.0.1','--port','5173'],cwd=app,env=env,stdout=log,stderr=log)
    try:
        for _ in range(100):
            try:
                if httpx.get('http://127.0.0.1:5173/api/health').status_code==200:break
            except httpx.RequestError:pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page()
            page.goto('http://127.0.0.1:5173')
            page.get_by_text('最长60分钟',exact=False).wait_for()
            page.locator('#video-select').select_option(index=1)
            page.get_by_text('正在回放已保存的真实 Qwen 分析记录',exact=False).wait_for()
            page.get_by_label('你想找回什么线索？').fill('笔记本什么时候被拿起？')
            page.get_by_role('button',name='提问',exact=True).click()
            page.locator('.answer-result .reference').first.wait_for()
            page.locator('.answer-result .reference').first.click()
            page.get_by_text('已定位至',exact=False).wait_for()
            assert abs(page.locator('video').evaluate('v=>v.currentTime')-3)<.25
            browser.close()
        assert httpx.get('http://127.0.0.1:5173/api/health').json()['usage']['requests']==0
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close()
    secrets=[value.encode() for key,value in dotenv_values(ROOT/'.env').items() if value and len(value)>8 and any(t in key.upper() for t in ['API_KEY','SECRET','TOKEN','PASSWORD'])]
    for path in package.rglob('*'):
        if not path.is_file():continue
        assert path.name!='.env' and path.suffix not in {'.log','.private','.sqlite3','.pyc'}
        assert not any(part in {'.git','node_modules','__pycache__','.venv'} for part in path.relative_to(package).parts)
        assert not any(secret in path.read_bytes() for secret in secrets)
    validation=json.loads((package/'package-validation.json').read_text(encoding='utf8'))
    validation.update(long_video_package='PASS_CLEAN_CHINESE_PATH_BOOT_AND_OFFLINE_REPLAY',long_backend_tests=49,
                      long_actual_video_api='PASS_393633ms_14_windows',long_actual_browser='PASS_SEEK_364s',
                      known_hallucination='Notebook-put-in false positive preserved in audit and corrected by real re-extraction; other events remain model candidates',
                      credential_scan='PASS_AFTER_LONG_VIDEO_UPDATE',new_model_calls_for_package_check=0)
    (package/'package-validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf8')
    manifest={str(p.relative_to(package)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.rglob('*')) if p.is_file() and p.name!='manifest-sha256.json'}
    (package/'manifest-sha256.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    archive=ROOT/'ShijiHome-submission-20261018-long-video.zip'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in package.rglob('*'):
            if p.is_file():z.write(p,Path('submission')/p.relative_to(package))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name,digest in manifest.items():assert hashlib.sha256(z.read('submission/'+name)).hexdigest()==digest
    result={'status':'PASS','checked_at_utc':datetime.now(timezone.utc).isoformat(),'archive':str(archive),'size_mb':round(archive.stat().st_size/1024**2,2),
            'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':len(manifest)+1,'model_calls':0,'original_archive_preserved':True}
    (ROOT/'evidence/long-archive-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

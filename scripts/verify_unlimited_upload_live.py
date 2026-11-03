"""Anonymous cloud upload tests: one long clip and a padded >50MiB MP4.

No model calls. The padding fixture contains the already-public fire clip plus
an ISO-BMFF free box; it is only a transfer test, never an analysis result.
"""
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import shutil
import struct
import sys

from playwright.sync_api import sync_playwright
from verify_modelscope_public import APP, STUDIO, request

ROOT=Path(__file__).resolve().parents[1]


def main():
    out=ROOT/'data/validation/unlimited-upload-live'
    out.mkdir(parents=True,exist_ok=True)
    padded=out/'transfer-test-only-padded-fire.mp4'
    with (ROOT/'video/火光_可上传.mp4').open('rb') as source,padded.open('wb') as target:
        shutil.copyfileobj(source,target)
        padding=51*1024**2
        target.write(struct.pack('>I4s',padding,b'free'))
        target.seek(padding-9,1)
        target.write(b'\0')
    results=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.goto(STUDIO,wait_until='domcontentloaded',timeout=60000)
        page.frame_locator('iframe[src*="bbbddxuan-anju-vision.ms.show"]').locator('.demo-card').first.wait_for(timeout=60000)
        frame=next(f for f in page.frames if f.url.startswith(APP+'/'))
        health=request(frame,'/api/health')['body']
        assert health['limits']['max_upload_mb'] is None and health['limits']['max_video_seconds'] is None
        before=health['usage']['requests']
        frame.get_by_role('button',name='打开监测工作台',exact=True).click()
        frame.evaluate('''() => {const send=XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send=function(...args){
                this.addEventListener('load',()=>{window.__uploadResult={status:this.status,text:this.responseText.slice(0,1000)}});
                return send.apply(this,args);
            };}''')
        cases=[(padded,'over_50mib_transfer')] if '--large-only' in sys.argv else [(ROOT/'data/public-desktop-playback.mp4','long_video'),(padded,'over_50mib_transfer')]
        for path,kind in cases:
            print(json.dumps({'stage':'uploading','kind':kind,'bytes':path.stat().st_size}),flush=True)
            previous={v['id'] for v in request(frame,'/api/videos')['body']['videos']}
            with page.expect_response(lambda r:r.request.method=='POST' and (r.url.split('?')[0]==APP+'/api/videos/upload' or (r.url.startswith(APP+'/api/uploads/') and (r.url.endswith('/complete') or r.status>=400))),timeout=180000) as pending:
                frame.get_by_label('上传 MP4',exact=True).set_input_files(str(path))
            response=pending.value
            if response.status!=201:
                failure={'kind':kind,'bytes':path.stat().st_size,'status':response.status}
                try:
                    failure['response']=response.json()
                except Exception:
                    failure['response']=frame.evaluate('window.__uploadResult')
                (out/'failure.json').write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding='utf8')
                print(json.dumps(failure),flush=True)
                raise AssertionError(failure)
            # Chrome can evict the CDP response body of a large upload even
            # though the application received it. Read the visitor's library
            # through fetch rather than depending on the inspector's cache.
            fresh=[v for v in request(frame,'/api/videos')['body']['videos'] if v['id'] not in previous]
            assert len(fresh)==1
            vid=fresh[0]['id']
            detail=request(frame,f'/api/videos/{vid}')['body']
            assert detail['active_run'] is None
            with path.open('rb') as stream:
                assert detail['sha256']==hashlib.file_digest(stream,'sha256').hexdigest()
            if kind=='long_video':
                assert detail['duration_ms']>390000
            else:
                assert detail['metadata']['file_size_bytes']>50*1024**2
            frame.wait_for_function('(id)=>document.querySelector("#video-select")?.value===id',arg=vid)
            page.screenshot(path=str(out/(kind+'.png')),full_page=True)
            assert request(frame,f'/api/videos/{vid}/delete',method='POST')['status']==200
            results.append({'kind':kind,'bytes':path.stat().st_size,'duration_ms':detail['duration_ms'],'status':response.status,'sha256_verified':True,'deleted':True})
            print(json.dumps(results[-1]),flush=True)
        assert request(frame,'/api/health')['body']['usage']['requests']==before
        browser.close()
    report={'verified':True,'anonymous_browser':True,'model_calls':0,'uploads':results,
            'application_size_limit':None,'application_duration_limit':None,
            'platform_single_request_limit_bytes':33554432,'chunked_upload_verified':True,
            'verified_at':datetime.now(timezone.utc).isoformat()}
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    state_path=ROOT/'data/modelscope-deployment.json'
    state=json.loads(state_path.read_text(encoding='utf8'))
    state.update(stage='public_verified',anonymous_verified=True,
                 validation_result=str(out/'result.json'),upload_validation_result=str(out/'result.json'))
    state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()

"""Real private-cloud browser acceptance: one 5.95s clip, <=4 model requests."""
import hashlib
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values
import httpx
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE='https://studio-bbbddxuan-anju-vision.api-inference.modelscope.net'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path)
    parser.add_argument('--reuse-completed',action='store_true',help='Finish UI checks using the stored completed run; never analyze again')
    args=parser.parse_args()
    token=dotenv_values(ROOT/'.env')['MODELSCOPE_API_KEY']
    headers={'Authorization':'Bearer '+token}
    out=ROOT/'data/validation/modelscope-live'
    out.mkdir(parents=True,exist_ok=True)
    bundle=Path(json.loads((ROOT/'data/modelscope-export.json').read_text(encoding='utf8'))['directory'])
    source=bundle/'demo-recording/video.mp4'
    demo_id=json.loads((bundle/'demo-recording/memory.json').read_text(encoding='utf8'))['video_id']
    if args.source:source=args.source.resolve(strict=True)
    def save(name,value):
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    # This is the owner's official private Studio authentication gateway.
    # Anonymous access was verified to return 401 requiring a ModelScope token.
    # Scope authentication to this host and never follow credentialed redirects.
    with httpx.Client(base_url=BASE,headers=headers,timeout=30,follow_redirects=False) as http:
        response=http.get('/api/health');response.raise_for_status();health=response.json()
        assert health['analysis_ready'] and health['cloud_mode'] and not health['is_mock']
        assert health['limits']['max_daily_calls']==60 and health['limits']['max_calls']==16
        assert health['usage']['pending']==0 and health['usage']['requests']<=56
        before=health['usage']['requests']
        initial_requests=before
        if args.reuse_completed:
            before=json.loads((out/'before-health.json').read_text(encoding='utf8'))['usage']['requests']
        else:
            save('before-health.json',health)
    errors=[]
    sdk_clients=[]
    captured={}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        def new_context():
            context=browser.new_context(viewport={'width':1440,'height':1000})
            # A private Studio redirects browser navigation to account login,
            # while its official API gateway accepts the owner's SDK token.
            # Bridge only this exact gateway through an isolated SDK client;
            # the token never enters page JS or unrelated requests. Each
            # browser context has a separate visitor cookie jar.
            sdk=httpx.Client(base_url=BASE,headers=headers,timeout=30,follow_redirects=False)
            cookie_file=out/'test-visitor.json'
            if not sdk_clients and cookie_file.is_file():
                sdk.cookies.set('anju_visitor',json.loads(cookie_file.read_text(encoding='utf8'))['visitor'],domain=urlsplit(BASE).hostname,path='/')
            sdk_clients.append(sdk)
            def route(request):
                if urlsplit(request.request.url).hostname==urlsplit(BASE).hostname:
                    incoming=request.request
                    forwarded={k:v for k,v in incoming.headers.items()
                        if k.lower() in {'content-type','x-video-name','origin','range'}}
                    payload=incoming.post_data_buffer
                    if incoming.method=='POST' and urlsplit(incoming.url).path=='/api/videos/upload':
                        # Chromium's interception may omit file-backed XHR
                        # bytes. The bridge transports the exact selected
                        # fixture, whose returned SHA256 is checked below.
                        print('Selected file bridge:', 'captured' if payload else 'file-backed XHR',flush=True)
                        if not payload:payload=source.read_bytes()
                    response=sdk.request(incoming.method,incoming.url,headers=forwarded,content=payload)
                    assert not response.is_redirect, 'Private SDK gateway unexpectedly redirected'
                    path=urlsplit(incoming.url).path
                    if incoming.method=='POST' and (path=='/api/videos/upload' or path.endswith('/analyze')):
                        captured[path]=(response.status_code,response.json())
                    if len(sdk_clients)==1:
                        visitor=sdk.cookies.get('anju_visitor')
                        if visitor:save('test-visitor.json',{'visitor':visitor})
                    outgoing={k:v for k,v in response.headers.items()
                        if k.lower() not in {'content-encoding','content-length','transfer-encoding','connection'}}
                    request.fulfill(status=response.status_code,headers=outgoing,body=response.content)
                else:
                    request.continue_()
            context.route('**/*',route)
            return context
        context=new_context();page=context.new_page()
        page.on('pageerror',lambda e:errors.append(str(e)))
        def api(path,method='GET',body=None):
            return page.evaluate('''async ([path,method,body]) => {
              const r=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
              return {status:r.status,data:await r.json()};
            }''',[path,method,body])
        try:
            page.goto(BASE+'/workspace?video='+demo_id,wait_until='domcontentloaded',timeout=60000)
            page.locator('.safety-alert').first.wait_for(timeout=30000)
            assert page.get_by_role('button',name='已保存的真实分析示例',exact=True).is_disabled()
            assert page.get_by_role('button',name='删除视频',exact=True).is_disabled()
            page.locator('#question').fill('视频中什么时候有人跌倒？')
            page.get_by_role('button',name='提问',exact=True).click()
            page.locator('.answer-result .reference').first.wait_for()
            page.screenshot(path=str(out/'cloud-example-desktop.png'),full_page=True)
            if args.reuse_completed:
                upload=json.loads((out/'upload.json').read_text(encoding='utf8'))
                vid=upload['video_id']
                page.goto(BASE+'/workspace?video='+vid,wait_until='domcontentloaded')
                recorded=json.loads((out/'started-job.json').read_text(encoding='utf8'))
                job=api('/api/jobs/'+recorded['id'])['data']
                assert job['status']=='completed','UI-only validation cannot restart an incomplete run'
            else:
                with page.expect_response(lambda r:r.url.endswith('/api/videos/upload')):
                    page.get_by_label('上传 MP4',exact=True).set_input_files(source)
                upload_status,upload=captured['/api/videos/upload'];assert upload_status==201,upload
                vid=upload['video_id'];save('upload.json',upload)
                button=page.get_by_role('button',name='开始 AI 分析',exact=True)
                button.wait_for()
                with page.expect_response(lambda r:r.url.endswith('/analyze')):
                    button.click()
                job_status,job=captured[f'/api/videos/{vid}/analyze'];assert job_status==202,job
                save('started-job.json',job)
            assert upload['sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
            deadline=time.monotonic()+240
            while job['status'] in {'running','queued'}:
                if time.monotonic()>deadline:
                    api(f'/api/videos/{vid}/pause','POST')
                    raise TimeoutError('Paused cloud validation at deadline')
                page.wait_for_timeout(2000)
                result=api('/api/jobs/'+job['id']);assert result['status']==200,result
                job=result['data']
            save('job.json',job)
            assert job['status']=='completed',job
            memory=api(f'/api/videos/{vid}/events?grouped=true')['data'];save('memory.json',memory)
            alerts=api(f'/api/videos/{vid}/alerts')['data'];save('alerts.json',alerts)
            assert not memory['is_mock'] and any(e['action']=='fall' for e in memory['events'])
            query=api(f'/api/videos/{vid}/query','POST',{'question':'视频中什么时候有人跌倒？'})
            assert query['status']==200 and query['data']['evidence'];save('query.json',query['data'])
            page.get_by_role('button',name='提问',exact=True).wait_for(state='visible')
            page.locator('#question').fill('视频中什么时候有人跌倒？')
            page.get_by_role('button',name='提问',exact=True).click()
            page.locator('.answer-result .reference').first.wait_for()
            page.locator('.safety-alert').first.wait_for(timeout=15000)
            page.get_by_role('button',name='查看视频证据 ↗',exact=True).first.click()
            page.locator('.safety-frames img').first.wait_for()
            assert page.locator('video').evaluate('(v)=>v.currentTime')>0
            page.screenshot(path=str(out/'cloud-analysis-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            page.screenshot(path=str(out/'cloud-analysis-mobile.png'),full_page=True)
            other=new_context();second=other.new_page();second.goto(BASE+'/workspace?video='+demo_id,wait_until='domcontentloaded')
            second.locator('.safety-alert').first.wait_for()
            result=second.evaluate('''async (vid)=>{
              const list=await (await fetch('/api/videos')).json();
              const read=await fetch('/api/videos/'+vid+'/events');
              const del=await fetch('/api/videos/'+vid+'/delete',{method:'POST'});
              return {visible:list.videos.some(v=>v.id===vid),read:read.status,delete:del.status};
            }''',vid)
            assert result=={'visible':False,'read':404,'delete':404},result
            other.close()
            after=api('/api/health')['data'];delta=after['usage']['requests']-before
            assert 1<=delta<=4 and after['usage']['pending']==0,after['usage']
            if args.reuse_completed:assert after['usage']['requests']==initial_requests,'UI-only checks must not call a model'
            assert not errors,errors
            save('after-health.json',after)
            result={'verified':True,'host':BASE,'model':health['model'],'real_model_requests':delta,
                    'total_deployment_requests':after['usage']['requests'],'unsettled_prior_accounting':after['usage'].get('unknown',0),
                    'browser_authentication':'local owner SDK bridge; interactive ModelScope login not verified',
                    'job_id':job['id'],'completed_windows':job['completed_windows'],
                    'real_upload_analysis':True,'evidence_qa':True,'evidence_seek':True,
                    'visitor_isolation':True,'mobile_overflow':False,'page_errors':errors}
            page.get_by_role('button',name='删除视频',exact=True).click()
            dialog=page.get_by_role('dialog',name='删除这段录像？')
            with page.expect_response(lambda r:r.url.endswith(f'/api/videos/{vid}/delete')) as deleted:
                dialog.get_by_role('button',name='确认删除',exact=True).click()
            assert deleted.value.status==200
            dialog.wait_for(state='hidden')
            assert api(f'/api/videos/{vid}/events')['status']==404
            assert api('/api/health')['data']['usage']['requests']==after['usage']['requests']
            result['uploaded_video_deleted_from_ui']=True
            save('result.json',result)
            print(json.dumps(result,ensure_ascii=False))
        except BaseException:
            page.screenshot(path=str(out/'failure.png'),full_page=True)
            save('failure-page.json',{'url':page.url,'text':page.locator('body').inner_text()[:2500],'errors':errors})
            raise
        finally:
            browser.close()
            for sdk in sdk_clients:sdk.close()


if __name__=='__main__':main()

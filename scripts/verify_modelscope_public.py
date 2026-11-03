"""Anonymous browser/API acceptance of the public Studio. No model calls or owner tokens."""
from datetime import datetime, timezone
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP = 'https://bbbddxuan-anju-vision.ms.show'
STUDIO = 'https://modelscope.cn/studios/BBBDDxuan/anju-vision'


def request(frame, path, *, method='GET', payload=None, headers=None):
    """Use the anonymous browser session established by ModelScope's public page.

    Direct HTTP clients do not receive the platform's browser session and can
    receive a misleading SDK-token error, even when no SDK token was supplied.
    No owner credential or persistent login profile is used here.
    """
    return frame.evaluate('''async ({path,method,payload,headers}) => {
        const options={method,headers:headers||{},credentials:'same-origin'};
        if(payload!==null){options.body=JSON.stringify(payload);options.headers['Content-Type']='application/json'}
        const r=await fetch(path,options);
        const bytes=await r.arrayBuffer();
        const body=(r.headers.get('content-type')||'').includes('application/json')
            ? JSON.parse(new TextDecoder().decode(bytes)) : null;
        return {status:r.status,byte_length:bytes.byteLength,body};
    }''', {'path':path,'method':method,'payload':payload,'headers':headers})


def main():
    out = ROOT/'data/validation/modelscope-public'
    out.mkdir(parents=True, exist_ok=True)
    errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1050})
        page.on('pageerror',lambda e:errors.append(str(e)))
        response=page.goto(STUDIO,wait_until='domcontentloaded',timeout=60000)
        assert response and response.status==200
        page.locator('iframe[src*="bbbddxuan-anju-vision.ms.show"]').wait_for(timeout=60000)
        page.frame_locator('iframe[src*="bbbddxuan-anju-vision.ms.show"]').locator('.demo-card').first.wait_for(timeout=60000)
        frame=next(f for f in page.frames if f.url.startswith(APP+'/'))
        response=request(frame,'/api/health')
        assert response['status']==200
        health=response['body']
        assert health['cloud_mode'] and health['limits']['max_daily_calls']==100
        assert health['limits']['max_upload_mb'] is None and health['limits']['max_video_seconds'] is None
        assert health['analysis_ready'] and not health['is_mock']
        before=health['usage']['requests']
        demos=request(frame,'/api/demos')['body']['demos']
        assert len(demos)==4 and {d['case_type'] for d in demos}=={'fire','day_fall','night_fall','long_video'}
        by_type={d['case_type']:d for d in demos}
        cases=[]
        for demo in demos:
            vid=demo['id']
            memory=request(frame,f'/api/videos/{vid}/events?grouped=true')['body']
            assert memory['fixture_kind']=='model_analysis' and not memory['is_mock'] and memory['events']
            media=request(frame,f'/media/videos/{vid}',headers={'Range':'bytes=0-1023'})
            assert media['status']==206 and media['byte_length']==1024
            assert request(frame,demo['poster_url'])['status']==200
            assert request(frame,f'/api/videos/{vid}/delete',method='POST')['status']==403
            assert request(frame,f'/api/videos/{vid}/analyze',method='POST')['status']==403
            cases.append({'id':vid,'case_type':demo['case_type'],'duration_ms':demo['duration_ms'],
                          'event_records':len(memory['events']),'priority_count':demo['priority_count']})
        for category,count in [('fire',3),('day_fall',1),('night_fall',1)]:
            answer=request(frame,f"/api/videos/{by_type[category]['id']}/query",method='POST',payload={
                'question':'视频中有哪些值得关注的情况？','use_model':False})['body']
            assert len(answer['observations'])+len(answer['inferences'])==count and answer['evidence']
        long_id=by_type['long_video']['id']
        summary=request(frame,f'/api/videos/{long_id}/query',method='POST',payload={
            'question':'概括这段录像里发生的事情','use_model':False})['body']
        assert summary['answer_style']=='summary' and len(summary['observations'])==1 and summary['evidence']
        assert frame.locator('.demo-card').count()==4
        for poster in frame.locator('.demo-poster img').all():
            poster.scroll_into_view_if_needed()
        frame.wait_for_function('() => [...document.querySelectorAll(".demo-poster img")].every(img=>img.complete&&img.naturalWidth>0)')
        frame.locator('.demo-gallery').scroll_into_view_if_needed()
        page.screenshot(path=str(out/'gallery-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert frame.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(out/'gallery-mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1050})
        frame.locator('.demo-card').filter(has_text='火光/明火').get_by_role('button',name='打开录像与证据 ↗',exact=True).click()
        frame.wait_for_function('() => document.querySelectorAll(".safety-alert.urgent").length===3',timeout=60000)
        assert frame.get_by_role('button',name='重新分析此案例',exact=True).is_enabled()
        assert '不设大小与时长上限' in frame.locator('.upload-zone').inner_text()
        assert frame.get_by_role('button',name='删除视频',exact=True).is_disabled()
        assert frame.get_by_role('button',name='标记已知悉',exact=True).count()==0
        frame.locator('.safety-alert').first.get_by_role('button',name='查看视频证据 ↗',exact=True).click()
        frame.locator('.safety-frames img').first.wait_for()
        frame.wait_for_function('() => [...document.querySelectorAll(".safety-frames img")].every(img=>img.complete&&img.naturalWidth>0)',timeout=60000)
        page.screenshot(path=str(out/'fire-evidence.png'),full_page=True)
        frame.get_by_role('link',name='产品介绍').click()
        frame.locator('.demo-card').filter(has_text='长视频').get_by_role('button',name='打开录像与证据 ↗',exact=True).click()
        frame.wait_for_function('() => document.querySelector("video")?.duration>390',timeout=60000)
        frame.wait_for_function('() => document.querySelector(".event-categories > button[aria-pressed=true]")?.textContent==="物体事件"')
        frame.locator('#timeline .event').first.wait_for()
        frame.locator('video').evaluate('v=>{v.currentTime=300;v.pause()}')
        frame.wait_for_function('() => Math.abs(document.querySelector("video").currentTime-300)<.3 && document.querySelector("video").readyState>=2',timeout=60000)
        page.screenshot(path=str(out/'long-evidence.png'),full_page=True)
        assert not errors,errors
        assert request(frame,'/api/health')['body']['usage']['requests']==before
        browser.close()
    report={'anonymous_verified':True,'browser_owner_login_required':False,'public_demo_count':4,
            'cases':cases,'model_calls':0,'daily_limit':100,'application_upload_size_limit':None,'application_duration_limit':None,'read_only_demos':True,'demo_reanalysis_button_enabled':True,'long_seek_300s':True,
            'gallery_mobile_overflow':False,'javascript_errors':errors,
            'verified_at':datetime.now(timezone.utc).isoformat(),'app_url':APP,'judge_url':STUDIO,
            'access_method':'public_studio_anonymous_browser_iframe'}
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    path=ROOT/'data/modelscope-deployment.json'
    deployment=json.loads(path.read_text(encoding='utf8'))
    deployment.update(stage='public_verified',anonymous_verified=True,validation_result=str(out/'result.json'))
    path.write_text(json.dumps(deployment,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()

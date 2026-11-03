"""One real, bounded public-browser reanalysis of the 9.9s fire case.

Uses no owner credentials. Expected 3 windows, at most 6 requests with the
configured single retry. Run only when a paid model smoke test is intended.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright
from verify_modelscope_public import APP, STUDIO, request

ROOT=Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def main():
    out=ROOT/'data/validation/demo-reanalysis-live'
    out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.goto(STUDIO,wait_until='domcontentloaded',timeout=60000)
        page.frame_locator('iframe[src*="bbbddxuan-anju-vision.ms.show"]').locator('.demo-card').first.wait_for(timeout=60000)
        frame=next(f for f in page.frames if f.url.startswith(APP+'/'))
        source=next(d for d in request(frame,'/api/demos')['body']['demos'] if d['case_type']=='fire')
        assert source['duration_ms']<10000
        source_id=source['id']
        original=digest(request(frame,f'/api/videos/{source_id}/events')['body'])
        before=request(frame,'/api/health')['body']['usage']['requests']
        frame.locator('.demo-card').filter(has_text='火光/明火').get_by_role('button',name='打开录像与证据 ↗',exact=True).click()
        button=frame.get_by_role('button',name='重新分析此案例',exact=True)
        button.wait_for()
        with page.expect_response(lambda r:r.url.split('?')[0]==APP+f'/api/videos/{source_id}/reanalyze' and r.request.method=='POST',timeout=60000) as pending:
            button.click()
        response=pending.value
        assert response.status==202, response.text()
        job=response.json()
        copy_id=job['video_id']
        assert copy_id!=source_id and not job['is_mock']
        print(json.dumps({'stage':'started','video_id':copy_id,'job_id':job['id']},ensure_ascii=False),flush=True)
        frame.wait_for_function('(id)=>document.querySelector("#video-select")?.value===id',arg=copy_id,timeout=30000)
        deadline=time.monotonic()+360
        last=None
        while time.monotonic()<deadline:
            job=request(frame,f"/api/jobs/{job['id']}")['body']
            state=(job['stage'],job.get('completed_windows'),job.get('calls'))
            if state!=last:
                print(json.dumps({'stage':job['stage'],'status':job['status'],'completed_windows':job.get('completed_windows'),'calls':job.get('calls')},ensure_ascii=False),flush=True)
                last=state
            assert job.get('calls',0)<=6
            if job['status'] not in {'queued','running'}:
                break
            page.wait_for_timeout(3000)
        if job['status'] in {'queued','running'}:
            request(frame,f'/api/videos/{copy_id}/pause',method='POST')
            raise RuntimeError('Bounded smoke test timed out; pause requested, no retries issued.')
        assert job['status']=='completed',job.get('error')
        memory=request(frame,f'/api/videos/{copy_id}/events')['body']
        detail=request(frame,f'/api/videos/{copy_id}')['body']
        assert memory['events'] and not memory['is_mock']
        assert memory['model_run']['input_video_sha256']==detail['sha256']
        assert detail['metadata']['reanalysis_of']==source_id and not detail['read_only_demo']
        assert digest(request(frame,f'/api/videos/{source_id}/events')['body'])==original
        frame.locator('#timeline .event').first.wait_for(timeout=30000)
        page.screenshot(path=str(out/'result.png'),full_page=True)
        after=request(frame,'/api/health')['body']['usage']['requests']
        deleted=request(frame,f'/api/videos/{copy_id}/delete',method='POST')
        assert deleted['status']==200
        assert request(frame,f'/api/videos/{source_id}/events')['status']==200
        assert request(frame,'/api/health')['body']['usage']['requests']==after
        report={'verified':True,'anonymous_browser':True,'source_id':source_id,'test_copy_id':copy_id,
            'job_id':job['id'],'status':job['status'],'model':job['model'],'event_count':len(memory['events']),
            'model_calls':after-before,'source_unchanged':True,'private_copy_deleted':True,
            'quota_preserved_after_delete':True,'verified_at':datetime.now(timezone.utc).isoformat()}
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
        browser.close()


if __name__=='__main__':
    main()

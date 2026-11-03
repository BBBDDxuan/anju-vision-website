"""Publish the user's Studio only after the new four-case runtime is ready. Never prints credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from dotenv import dotenv_values
import httpx

ROOT = Path(__file__).resolve().parents[1]
NAME = 'BBBDDxuan/anju-vision'
CONTROL = 'https://modelscope.cn/openapi/v1'
GATEWAY = 'https://studio-bbbddxuan-anju-vision.api-inference.modelscope.net'


def main():
    token = dotenv_values(ROOT/'.env')['MODELSCOPE_API_KEY']
    expected = {'fire','day_fall','night_fall','long_video'}
    # The owner token is scoped to the exact official control plane and this
    # owner's exact SDK gateway. Credentialed redirects are never followed.
    with httpx.Client(base_url=CONTROL, headers={'Authorization':'Bearer '+token},timeout=40,follow_redirects=False) as control, \
         httpx.Client(base_url=GATEWAY,headers={'Authorization':'Bearer '+token},timeout=40,follow_redirects=False) as sdk:
        last = None
        for attempt in range(24):
            r = control.get('/studios/'+NAME); r.raise_for_status(); studio = r.json()['data']
            state = studio['status']
            if state != last:
                print(json.dumps({'status':state,'visibility':studio['visibility']},ensure_ascii=False),flush=True)
                last = state
            if state == 'Running':
                try:
                    h = sdk.get('/api/health'); h.raise_for_status(); health = h.json()
                    d = sdk.get('/api/demos'); d.raise_for_status(); demos = d.json()['demos']
                    if ({v['case_type'] for v in demos} == expected and len(demos)==4
                            and health['limits']['max_daily_calls']==100 and health['analysis_ready']
                            and health['limits']['max_upload_mb'] is None
                            and health['limits']['max_video_seconds'] is None):
                        break
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
            if state in {'BuildFailed','Failed','Error'}:
                raise RuntimeError('Deployment failed; inspect sanitized build/run logs before publishing.')
            time.sleep(15)
        else:
            raise RuntimeError('Four-case runtime is not ready; Studio remains at its current visibility.')
        assert studio['hardware'].startswith('platform/'), 'Do not switch to paid hardware.'
        assert next(v for v in demos if v['case_type']=='long_video')['duration_ms'] > 390000
        assert next(v for v in demos if v['case_type']=='fire')['priority_count']==3
        print('Verified four-case runtime, unlimited application upload limits and daily cap 100.',flush=True)
        if studio['visibility'] != 'public':
            r=control.patch('/studios/'+NAME+'/settings',json={'visibility':'public'});r.raise_for_status()
            if r.json().get('success') is False:
                raise RuntimeError('Visibility update was not accepted.')
        r=control.get('/studios/'+NAME);r.raise_for_status();studio=r.json()['data']
        assert studio['visibility']=='public'
    report_path=ROOT/'data/modelscope-deployment.json'
    report=json.loads(report_path.read_text(encoding='utf8'))
    report.update(visibility='public',stage='public_published',deployed=True,
                  public_demo_count=4,published_at=datetime.now(timezone.utc).isoformat(),
                  anonymous_verified=False,cloud_daily_limit=100)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    export_path=ROOT/'data/modelscope-export.json'
    export=json.loads(export_path.read_text(encoding='utf8'));export['docker_build_verified']=True
    export_path.write_text(json.dumps(export,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'visibility':'public','studio_url':'https://modelscope.cn/studios/'+NAME,
                      'app_url':'https://bbbddxuan-anju-vision.ms.show','public_demo_count':4},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

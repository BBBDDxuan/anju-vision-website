"""Recover the already-processed private validation run; forbid new model calls."""
import json
import shutil
from pathlib import Path
import time

from dotenv import dotenv_values
import httpx

ROOT=Path(__file__).resolve().parents[1]
BASE='https://studio-bbbddxuan-anju-vision.api-inference.modelscope.net'


def main():
    folder=ROOT/'data/validation/modelscope-live'
    previous=json.loads((folder/'started-job.json').read_text(encoding='utf8'))
    visitor=json.loads((folder/'test-visitor.json').read_text(encoding='utf8'))['visitor']
    out=folder/'previous-run-recovery';out.mkdir(exist_ok=True)
    for name in ['before-health.json','started-job.json','upload.json']:
        if not (out/name).exists():shutil.copy2(folder/name,out/name)
    with httpx.Client(base_url=BASE,headers={'Authorization':'Bearer '+dotenv_values(ROOT/'.env')['MODELSCOPE_API_KEY']},
                      cookies={'anju_visitor':visitor},timeout=30,follow_redirects=False) as sdk:
        def call(path,method='GET'):
            response=sdk.request(method,path);response.raise_for_status();return response.json()
        before=call('/api/health')['usage']['requests']
        job=call('/api/jobs/'+previous['id'])
        if job['status']!='completed':
            assert job.get('pending_windows')==0 and job['completed_windows']==job['total_windows'], 'Do not resume unprocessed windows'
            job=call('/api/videos/'+previous['video_id']+'/resume?max_calls=1&auto_continue=false','POST')
            deadline=time.monotonic()+60
            while job['status'] in {'running','queued'}:
                assert time.monotonic()<deadline,'Cached recovery timed out'
                time.sleep(1)
                job=call('/api/jobs/'+previous['id'])
        (out/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf8')
        assert job['status']=='completed',job.get('error')
        memory=call('/api/videos/'+previous['video_id']+'/events?grouped=true')
        (out/'memory.json').write_text(json.dumps(memory,ensure_ascii=False,indent=2),encoding='utf8')
        after=call('/api/health')['usage']['requests']
        assert after==before,'Cached recovery must not issue model requests'
        result={'completed':True,'new_model_calls':0,'job_id':job['id'],'events':len(memory['events'])}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

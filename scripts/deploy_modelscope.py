"""Deploy the verified export on free hardware, retaining existing visibility."""
import json
import hashlib
from ipaddress import ip_address
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from dotenv import dotenv_values
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.config import Settings


def main():
    local=dotenv_values(ROOT/'.env')
    token=os.environ.get('MODELSCOPE_API_KEY') or local.get('MODELSCOPE_API_KEY')
    if not token: raise RuntimeError('Configure MODELSCOPE_API_KEY locally first.')
    settings=Settings.from_env()
    if not settings.api_key: raise RuntimeError('Local VLM_API_KEY is required for real upload analysis.')
    endpoint=(local.get('MODELSCOPE_ENDPOINT') or 'https://modelscope.cn').rstrip('/')
    if endpoint not in {'https://modelscope.cn','https://www.modelscope.cn'}:
        raise RuntimeError('This deployment targets the domestic ModelScope site only.')
    export=json.loads((ROOT/'data/modelscope-export.json').read_text(encoding='utf8'))
    validation=json.loads((ROOT/'data/validation/modelscope-bundle/result.json').read_text(encoding='utf8'))
    assert export['directory']==validation['bundle'] and validation['verified']
    bundle=Path(export['directory'])
    hide=[token,settings.api_key]

    def safe(text):
        for value in hide:
            text=text.replace(value,'[REDACTED]')
        return text

    with httpx.Client(base_url=endpoint+'/openapi/v1',headers={'Authorization':'Bearer '+token},timeout=45) as client:
        def api(method,path,payload=None,missing=False):
            r=client.request(method,path,json=payload)
            if missing and r.status_code==404: return None
            if r.status_code>=400:
                try: message=r.json().get('message',r.json().get('error',''))
                except ValueError: message='non-JSON response'
                raise RuntimeError(safe(f'{method} {path}: HTTP {r.status_code} {message}'))
            data=r.json()
            if data.get('success') is False:
                raise RuntimeError(safe(str(data.get('message','API returned failure'))))
            return data.get('data',data)

        owner=api('GET','/users/me')['username']
        repo='anju-vision'
        name=owner+'/'+repo
        hardware=api('GET','/studios/hardware?sdk_type=docker')['hardware']
        free=next((h for h in hardware if h.get('resource_type')=='free' and 'docker' in h.get('supported_sdk_types',[])),None)
        if not free: raise RuntimeError('No free Docker resource available. Paid resources were not selected.')
        studio=api('GET','/studios/'+name,missing=True)
        if studio is None:
            studio=api('POST','/studios',{'owner':owner,'repo_name':repo,'sdk_type':'docker',
                'display_name':'安居视界','description':'独居老人关怀与家庭危险识别，支持录像上传、真实AI分析和证据问答。',
                'visibility':'private','hardware':free['name']})
            print('Created private Studio:',name,flush=True)
        else:
            if studio.get('visibility') not in {'private','public'}:
                raise RuntimeError('Unsupported Studio visibility; inspect before deployment.')
            api('PATCH','/studios/'+name+'/settings',{'sdk_type':'docker','hardware':free['name']})
        report={'owner':owner,'repo':repo,'url':endpoint+'/studios/'+name,
                'visibility':studio.get('visibility','private'),'hardware':free['name'],
                'stage':'created','deployed':False,'anonymous_verified':False,
                'public_demo_count':4,'cloud_daily_limit':100}
        report_path=ROOT/'data/modelscope-deployment.json'
        def save(): report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        save()

        checkout=ROOT/'data/modelscope-git-checkout'
        helper=ROOT/'data/modelscope-git-credential.py'
        helper.write_text("import os,sys\nif len(sys.argv)>1 and sys.argv[1]=='get':\n print('username=oauth2')\n print('password='+os.environ['MODELSCOPE_API_KEY'])\n",encoding='utf8')
        helper_cmd='!'+json.dumps(sys.executable.replace('\\','/'))+' '+json.dumps(str(helper).replace('\\','/'),ensure_ascii=False)
        git_env={**os.environ,'MODELSCOPE_API_KEY':token,'GIT_TERMINAL_PROMPT':'0',
                 'GIT_TRACE':'0','GIT_TRACE_CURL':'0','GIT_CURL_VERBOSE':'0'}
        def git(*args):
            command=['git','-c','credential.helper=','-c','credential.helper='+helper_cmd,*args]
            result=subprocess.run(command,cwd=checkout if checkout.exists() else ROOT/'data',env=git_env,
                                  capture_output=True,text=True,encoding='utf8',errors='replace',timeout=180)
            if result.returncode: raise RuntimeError(safe(result.stderr[-2500:]))
            return safe(result.stdout.strip())
        remote=endpoint+'/studios/'+name+'.git'
        if not checkout.exists():
            git('clone',remote,str(checkout))
            git('checkout','-b','codex/modelscope-deploy')
        else:
            existing=git('remote','get-url','origin')
            if existing!=remote: raise RuntimeError('Checkout belongs to another remote; preserving it.')
            git('fetch','origin','master')
            git('merge','--ff-only','origin/master')
        files=[]
        for path in bundle.rglob('*'):
            if not path.is_file() or '.git' in path.parts or '__pycache__' in path.parts: continue
            relative=path.relative_to(bundle)
            if relative.parts[:2]==('frontend','dist'): continue
            content=path.read_bytes()
            if any(secret.encode() in content for secret in hide): raise RuntimeError('Secret found in export; refusing push.')
            destination=checkout/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,destination)
            files.append(relative.as_posix())
        git('add','--',*files)
        git('add','--renormalize','--','demo-recording','demo-recordings')
        if git('diff','--cached','--name-only'):
            git('-c','user.name=Anju Vision','-c','user.email=anju-vision@users.noreply.local',
                'commit','-m','Deploy Anju Vision with isolated visitor uploads and evidence QA')
        # Check committed bytes too: a local hash check alone misses Git's CRLF
        # conversion, which otherwise breaks the Linux importer's integrity gate.
        folders = ['demo-recording'] + [item['directory'] for item in json.loads((bundle/'demo-catalog.json').read_text(encoding='utf8'))['demos']]
        for folder in folders:
            manifest=json.loads((bundle/folder/'manifest.json').read_text(encoding='utf8'))
            for relative,expected in manifest['sha256'].items():
                blob=subprocess.run(['git','show','HEAD:'+folder+'/'+relative],cwd=checkout,
                                    capture_output=True,check=True).stdout
                if hashlib.sha256(blob).hexdigest()!=expected:
                    raise RuntimeError('Committed demo bytes differ from verified source: '+relative)
        git('push','origin','HEAD:master')
        report.update(stage='code_pushed',commit=git('rev-parse','HEAD'));save()

        variables={'CLOUD_MODE':'true','CLOUD_COOKIE_SECURE':'true','DATA_DIR':'/mnt/workspace/anju-data',
            'VLM_PROVIDER':'qwen','VLM_MODEL':settings.model,'VLM_BASE_URL':settings.base_url,
            'VLM_ENABLE_NETWORK':'true','RECORD_QA_ENABLED':'true','VLM_BILLING_MODE':'request_limited','MAX_MODEL_CALLS_PER_DAY':'100',
            'MAX_MODEL_CALLS_PER_VIDEO':'16','MAX_FRAME_REFERENCES':'256','MAX_OUTPUT_TOKENS_PER_VIDEO':'32768',
            'MAX_CONCURRENCY':'1','MAX_VIDEO_SECONDS':'0','MAX_UPLOAD_MB':'0',
            'CLOUD_MAX_VIDEOS':'20','CLOUD_VIDEOS_PER_VISITOR':'3','CLOUD_STORAGE_MB':'0',
            'APP_ALLOWED_HOSTS':'127.0.0.1,localhost,'+owner.lower()+'-'+repo+'.ms.show,studio-'+owner.lower()+'-'+repo+'.api-inference.modelscope.net',
            'APP_ALLOWED_ORIGINS':'https://'+owner.lower()+'-'+repo+'.ms.show,https://studio-'+owner.lower()+'-'+repo+'.api-inference.modelscope.net',
            'FFMPEG_PATH':'/usr/bin/ffmpeg','FFPROBE_PATH':'/usr/bin/ffprobe'}
        proxy_file=ROOT/'data/modelscope-proxy-peer.json'
        if proxy_file.is_file():
            peer=ip_address(json.loads(proxy_file.read_text(encoding='utf8'))['exact_ip'])
            variables['APP_TRUSTED_PROXY_CIDRS']=f'{peer}/{peer.max_prefixlen}'
        def names(data,field):
            rows=data.get(field,[]) if isinstance(data,dict) else data
            return {r.get('key',r.get('name')) if isinstance(r,dict) else r for r in rows}
        present=names(api('GET','/studios/'+name+'/variables'),'variables')
        for key,value in variables.items():
            api('PUT' if key in present else 'POST','/studios/'+name+'/variables',{'key':key,'value':value})
        present=names(api('GET','/studios/'+name+'/secrets'),'secrets')
        api('PUT' if 'VLM_API_KEY' in present else 'POST','/studios/'+name+'/secrets',{'key':'VLM_API_KEY','value':settings.api_key})
        report.update(stage='configured',model=settings.model);save()
        api('POST','/studios/'+name+'/deploy')
        report.update(stage='build_requested');save()
        print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()

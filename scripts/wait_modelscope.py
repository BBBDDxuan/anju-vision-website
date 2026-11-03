"""Bounded, read-only deployment monitor; emits only state changes."""
import json
from pathlib import Path
import time

from dotenv import dotenv_values
import httpx

ROOT=Path(__file__).resolve().parents[1]
token=dotenv_values(ROOT/'.env')['MODELSCOPE_API_KEY']
last=None
with httpx.Client(headers={'Authorization':'Bearer '+token},timeout=25) as client:
    for attempt in range(16):
        response=client.get('https://modelscope.cn/openapi/v1/studios/BBBDDxuan/anju-vision')
        response.raise_for_status()
        data=response.json()['data']
        state=data.get('status')
        (ROOT/'data/modelscope-latest-state.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
        if state!=last:
            print(json.dumps({'status':state,'host':data.get('host'),'visibility':data.get('visibility')},ensure_ascii=False),flush=True)
            last=state
        if state not in {'Building','Starting','Pending','Deploying'}:
            break
        time.sleep(15)
    else:
        print('Bounded monitor ended; inspect latest state/logs before continuing.',flush=True)

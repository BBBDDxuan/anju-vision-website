"""Read-only, redacted Studio state/build/runtime logs."""
import json
import os
from pathlib import Path

from dotenv import dotenv_values
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    values=dotenv_values(ROOT/'.env')
    token=os.environ.get('MODELSCOPE_API_KEY') or values['MODELSCOPE_API_KEY']
    hidden=[token,values.get('VLM_API_KEY',''),values.get('DASHSCOPE_API_KEY','')]
    def clean(text):
        for key in hidden:
            if key:text=text.replace(key,'[REDACTED]')
        return text
    base='https://modelscope.cn/openapi/v1/studios/BBBDDxuan/anju-vision'
    with httpx.Client(headers={'Authorization':'Bearer '+token},timeout=30) as client:
        response=client.get(base);response.raise_for_status()
        data=response.json()['data']
        (ROOT/'data/modelscope-latest-state.json').write_text(clean(json.dumps(data,ensure_ascii=False,indent=2)),encoding='utf8')
        print(json.dumps({k:data.get(k) for k in ['id','status','visibility','hardware','host']},ensure_ascii=False),flush=True)
        for kind in ['build','run']:
            r=client.get(base+'/logs/'+kind);r.raise_for_status()
            payload=r.json().get('data',{})
            lines=payload.get('logs',[]) if isinstance(payload,dict) else payload
            content=clean(''.join(str(line) for line in lines))
            (ROOT/f'data/modelscope-{kind}.log').write_text(content,encoding='utf8')
            print(kind+':\n'+content[-4500:],flush=True)


if __name__=='__main__':main()

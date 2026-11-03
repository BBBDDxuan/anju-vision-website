"""Evaluate downloaded fall-specific detector offline without selecting it for deployment."""
from pathlib import Path
from contextlib import redirect_stdout
import importlib,json,sys,time,os
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'elder-safety/ai-service'))
from ai_service.runtime_env import configure_model_runtime
configure_model_runtime()
import cv2
import torch
import numpy as np
from ultralytics import YOLO

out=ROOT/'runtime/fall-corpus-20261101/learned-evaluation';out.mkdir(parents=True,exist_ok=True)
research=ROOT/'runtime/fall-corpus-20261101/research'
weights=research/'melih-yolo11-fall.pt'
audit=json.loads((research/'melih-download-audit.json').read_text(encoding='utf-8'))
allowed=[]
for name in audit['globals']:
 module,attribute=name.split(' ')
 if module=='__builtin__':module='builtins'
 assert module.startswith(('ultralytics.nn.','torch.')) or module in ('builtins','collections','torch')
 allowed.append(getattr(importlib.import_module(module),attribute))
with torch.serialization.safe_globals(allowed):
 checkpoint=torch.load(weights,map_location='cpu',weights_only=True)
assert isinstance(checkpoint,dict)
del checkpoint
with open(os.devnull,'w') as quiet,redirect_stdout(quiet):model=YOLO(str(weights),task='detect')
print('Labels:',model.names,flush=True)
paths=sorted((ROOT/'video/public_samples/CAUCAFall_validation_9').glob('*.mp4'))
paths+=[ROOT/'video/public_samples/CAUCAFall_Subject1_FallForward/CAUCAFall_S1_simulated_fall_forward.avi',ROOT/'video/10月30日.mp4']
summary={'model_names':model.names,'scope':'frame detections only; not confirmed falls','cloud_calls':0,'clips':[]}
for source in paths:
 cap=cv2.VideoCapture(str(source));assert cap.isOpened();fps=cap.get(cv2.CAP_PROP_FPS);index=0;next_t=0;rows=[];start=time.perf_counter()
 while True:
  ok,image=cap.read()
  if not ok:break
  t=index/fps
  if t+1e-8>=next_t:
   with open(os.devnull,'w') as quiet,redirect_stdout(quiet):r=model.predict(image,device='0',verbose=False,conf=.1)[0]
   detections=[{'bbox':b.tolist(),'confidence':float(c),'label':model.names[int(k)]} for b,c,k in zip(r.boxes.xyxy.cpu().numpy(),r.boxes.conf.cpu().numpy(),r.boxes.cls.cpu().numpy())]
   rows.append({'t':t,'index':index,'detections':detections});next_t=(int((t+1e-8)*10)+1)/10
  index+=1
 cap.release()
 (out/f'{source.stem}.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
 falls=[(r['t'],d['confidence']) for r in rows for d in r['detections'] if 'fall' in d['label'].lower() and d['confidence']>=.6]
 item={'source':str(source),'frames':index,'samples':len(rows),'seconds':round(time.perf_counter()-start,3),'fall_frame_count_conf_06':len(falls),'fall_times_conf_06':falls}
 summary['clips'].append(item);(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
 print(source.stem,len(falls),'first',falls[:3],flush=True)

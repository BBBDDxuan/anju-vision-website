"""Local pose-only fall regression; saves predictions for reproducible replay.
No cloud calls, recording changes or model downloads.
"""
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'elder-safety/ai-service'))
from ai_service.runtime_env import configure_model_runtime
configure_model_runtime()
import cv2
import numpy as np
from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.types import Pose, Sample


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--replay', type=Path)
    p.add_argument('--fps', type=float, default=10)
    p.add_argument('--review-candidates', action='store_true')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = Config.load(str(ROOT/'elder-safety/ai-service/config.camera-gpu.json'))
    engine = DetectionEngine(config, allow_review_candidates=args.review_candidates)
    start = time.perf_counter()
    cap = cv2.VideoCapture(str(args.source))
    assert cap.isOpened(), 'Cannot open source'
    fps, total = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    models = None
    cached = None
    if args.replay:
        cached = [json.loads(line) for line in args.replay.read_text(encoding='utf-8').splitlines()]
    else:
        from ai_service.models import LocalModels
        with open(os.devnull, 'w') as quiet, redirect_stdout(quiet):
            models = LocalModels(replace(config.models, flame_weights=None))
    origin = datetime(2026, 10, 31, tzinfo=timezone.utc)
    records, alerts, tiles = [], [], []
    index, next_t, next_tile = 0, 0., 0.
    def handle(index, t, poses, image):
        signals = engine.process(Sample(image, t, origin+timedelta(seconds=t), poses=poses, flames_observed=False))
        diagnostics = {}
        for track, diag in engine.fall.diagnostics.items():
            state = engine.fall.states.get(track)
            extras = {k:getattr(state,k) for k in ('armed_at','candidate_at','horizontal_since','motion_at','peak_speed','peak_rotation_speed')} if state else {}
            diagnostics[track] = {**diag, **extras}
        sigs = [asdict(s) for s in signals]
        records.append({'index':index, 't':t, 'poses':[{'bbox':p.bbox,'confidence':p.confidence,'keypoints':np.asarray(p.keypoints).tolist()} for p in poses], 'diagnostics':diagnostics, 'signals':sigs})
        alerts.extend({'t':t,'index':index, **s} for s in sigs)
    if cached is not None:
        for item in cached:
            handle(item['index'],item['t'],[Pose(tuple(p['bbox']),np.array(p['keypoints']),p['confidence']) for p in item['poses']],np.zeros((2,2,3),np.uint8))
    else:
        while True:
            ok, image = cap.read()
            if not ok: break
            t = index/fps
            if t+1e-8 >= next_t:
                with open(os.devnull,'w') as quiet, redirect_stdout(quiet):
                    poses = models.infer_pose(image)
                handle(index,t,poses,image)
                next_t = (int((t+1e-8)*args.fps)+1)/args.fps
                if t+1e-8 >= next_tile and len(tiles) < 120:
                    thumb=cv2.resize(image,(384,216))
                    sx,sy=384/image.shape[1],216/image.shape[0]
                    for pose in poses:
                        x1,y1,x2,y2=pose.bbox
                        cv2.rectangle(thumb,(int(x1*sx),int(y1*sy)),(int(x2*sx),int(y2*sy)),(0,255,255),1)
                        for x,y,c in pose.keypoints:
                            if c>=config.fall.keypoint_confidence: cv2.circle(thumb,(int(x*sx),int(y*sy)),2,(0,255,0),-1)
                    thumb=cv2.copyMakeBorder(thumb,0,40,0,0,cv2.BORDER_CONSTANT,value=(25,25,25))
                    cv2.putText(thumb,f't={t:.2f}s', (5,233),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,255),1)
                    state=','.join(d['state'] for d in records[-1]['diagnostics'].values()) or 'no_pose'
                    cv2.putText(thumb,state[:48],(5,250),cv2.FONT_HERSHEY_SIMPLEX,.4,(255,255,255),1)
                    tiles.append(thumb)
                    next_tile=t+max(.5,total/fps/120)
            index+=1
    cap.release()
    (args.output/'poses.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in records),encoding='utf-8')
    if tiles:
        for page in range((len(tiles)+39)//40):
            group=tiles[page*40:(page+1)*40]
            canvas=np.full((((len(group)+3)//4)*256,4*384,3),25,np.uint8)
            for i,tile in enumerate(group):canvas[(i//4)*256:(i//4+1)*256,(i%4)*384:(i%4+1)*384]=tile
            ok,data=cv2.imencode('.jpg',canvas);assert ok
            (args.output/f'contact-{page}.jpg').write_bytes(data.tobytes())
    summary={'source':str(args.source.resolve()),'source_sha256':hashlib.sha256(args.source.read_bytes()).hexdigest(),'source_fps':fps,'source_frames':total,'inferences':0 if cached is not None else len(records),'samples':len(records),'review_candidates':args.review_candidates,'pose_only':True,'cloud_calls':0,'replay':str(args.replay) if args.replay else None,'wall_seconds':round(time.perf_counter()-start,3),'thresholds':asdict(config.fall),'alerts':alerts,'source_hashes':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['elder-safety/ai-service/ai_service/temporal.py','elder-safety/ai-service/ai_service/engine.py','elder-safety/ai-service/ai_service/tracking.py','elder-safety/ai-service/ai_service/fall_candidates.py']}}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__': main()

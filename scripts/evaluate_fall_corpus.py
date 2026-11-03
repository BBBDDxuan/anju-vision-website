"""Offline fall corpus evaluation. No cloud, recording or alerts are sent.

Save raw predictions, strict/review candidate timings, SHA256 and contact sheets.
Use --replay-root to compare detector changes on identical predictions.
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
sys.path.insert(0, str(ROOT/'elder-safety/ai-service'))
from ai_service.runtime_env import configure_model_runtime
configure_model_runtime()
import cv2
import numpy as np
from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.types import Pose, Sample, iou


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--replay-root',type=Path)
    parser.add_argument('--weights',type=Path)
    parser.add_argument('--rotations',action='store_true')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    config=Config.load(str(ROOT/'elder-safety/ai-service/config.camera-gpu.json'))
    config.models=replace(config.models,flame_weights=None)
    if args.rotations: config.models=replace(config.models,pose_fall_recovery_enabled=False)
    if args.weights: config.models=replace(config.models,pose_weights=str(args.weights.resolve()))
    config.recording.enabled=False
    models=None;started=time.perf_counter()
    if not args.replay_root:
        from ai_service.models import LocalModels
        with open(os.devnull,'w') as quiet,redirect_stdout(quiet):models=LocalModels(config.models)
    report={'pose_only':True,'cloud_calls':0,'inference_fps':10,'model_load_seconds':time.perf_counter()-started,
            'weights':config.models.pose_weights,'weights_sha256':hashlib.sha256(Path(config.models.pose_weights).read_bytes()).hexdigest(),
            'rotations':args.rotations,'replayed':bool(args.replay_root),'source_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'elder-safety/ai-service/ai_service').glob('*.py'))},'clips':[]}
    sources=sorted(args.source_dir.glob('*.mp4'))+sorted(args.source_dir.glob('*.avi'))
    assert sources,'No clips selected'
    for source in sources:
        output=args.output/source.stem;output.mkdir(exist_ok=True)
        engines={'strict':DetectionEngine(config),'review':DetectionEngine(config,allow_review_candidates=True)}
        signals={key:[] for key in engines};records=[];tiles=[]
        counters={key:getattr(models,key,0) for key in ('pose_frames','pose_predict_calls','pose_recovery_frames')}
        cap=cv2.VideoCapture(str(source));assert cap.isOpened()
        fps=cap.get(cv2.CAP_PROP_FPS);total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration=total/fps;index=0;next_t=0.;next_tile=0.;start=time.perf_counter()
        origin=datetime(2026,11,1,tzinfo=timezone.utc)
        if args.replay_root:
            raw=[json.loads(line) for line in (args.replay_root/source.stem/'poses.jsonl').read_text(encoding='utf-8').splitlines()]
        else:raw=None
        def handle(item,image):
            poses=[Pose(tuple(p['bbox']),np.asarray(p['keypoints']),p['confidence']) for p in item['poses']]
            t=item['t'];row={**item,'engines':{}}
            for key,engine in engines.items():
                found=engine.process(Sample(image,t,origin+timedelta(seconds=t),poses=poses,flames_observed=False))
                signals[key].extend({'t':t,**asdict(s)} for s in found)
                row['engines'][key]={'diagnostics':engine.fall.diagnostics.copy(),'signals':[asdict(s) for s in found]}
            records.append(row)
        if raw is not None:
            for item in raw:handle({'index':item['index'],'t':item['t'],'poses':item['poses']},np.zeros((2,2,3),np.uint8))
        else:
            while True:
                ok,image=cap.read()
                if not ok:break
                t=index/fps
                if t+1e-8>=next_t:
                    with open(os.devnull,'w') as quiet,redirect_stdout(quiet):
                        poses=models.infer_pose(image)
                        if args.rotations:
                            h,w=image.shape[:2]
                            extras=[]
                            for rotation in (cv2.ROTATE_90_CLOCKWISE,cv2.ROTATE_90_COUNTERCLOCKWISE):
                                for pose in models.infer_pose(cv2.rotate(image,rotation)):
                                    kp=pose.keypoints.copy();box=np.array(pose.bbox).reshape(2,2)
                                    def remap(points):
                                        xy=points.copy()
                                        if rotation==cv2.ROTATE_90_CLOCKWISE:
                                            xy[:,0]=points[:,1];xy[:,1]=h-1-points[:,0]
                                        else:
                                            xy[:,0]=w-1-points[:,1];xy[:,1]=points[:,0]
                                        return xy
                                    kp[:,:2]=remap(kp[:,:2]);corners=remap(box)
                                    mapped=tuple(np.concatenate((corners.min(axis=0),corners.max(axis=0))).tolist())
                                    if pose.confidence>=.35 and kp[[5,6,11,12],2].min()>=.55:
                                        extras.append(Pose(mapped,kp,pose.confidence))
                            merged=[]
                            for pose in sorted(poses+extras,key=lambda p:float(p.keypoints[[5,6,11,12],2].min())*p.confidence,reverse=True):
                                if not any(iou(pose.bbox,old.bbox)>.3 for old in merged):merged.append(pose)
                            poses=merged
                    item={'index':index,'t':t,'poses':[{'bbox':p.bbox,'confidence':p.confidence,'keypoints':p.keypoints.tolist()} for p in poses]}
                    handle(item,image);next_t=(int((t+1e-8)*10)+1)/10
                    if t+1e-8>=next_tile:
                        tile=cv2.resize(image,(320,240));tile=cv2.copyMakeBorder(tile,0,24,0,0,cv2.BORDER_CONSTANT,value=(25,25,25))
                        cv2.putText(tile,f'{t:.2f}s',(8,258),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
                        tiles.append(tile);next_tile=t+max(.3,duration/24)
                index+=1
        cap.release()
        if not args.replay_root:assert index==total
        (output/'poses.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in records),encoding='utf-8')
        if tiles:
            canvas=np.full((((len(tiles)+3)//4)*264,1280,3),25,np.uint8)
            for i,tile in enumerate(tiles):canvas[(i//4)*264:(i//4+1)*264,(i%4)*320:(i%4+1)*320]=tile
            ok,data=cv2.imencode('.jpg',canvas);assert ok;(output/'contact.jpg').write_bytes(data.tobytes())
        record={'source':str(source.resolve()),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'fps':fps,'frames':total,'duration_seconds':duration,'samples':len(records),'wall_seconds':time.perf_counter()-start,'signals':signals}
        record['pose_inference_counts']={key:getattr(models,key,0)-value for key,value in counters.items()}
        (output/'summary.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        report['clips'].append(record)
        (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(source.stem, {k:[(s['event_type'],s['t']) for s in ss] for k,ss in signals.items()},flush=True)
    print('Completed',len(sources),'clips',flush=True)

if __name__=='__main__': main()
